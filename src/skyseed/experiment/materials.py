"""Selected file-backed material catalog; runtime alone adopts after handoff."""
from pathlib import Path
import json
from threading import RLock
from .output.occupancy import claim_runtime
from .interaction import InteractionClient


def _verify_initial_reference(definition, manifest_path):
    """Check a selected initial header against fixed generated inputs, without binary I/O."""
    from hashlib import sha256
    handoff = definition['runner_artifact']
    raw = Path(manifest_path).read_bytes()
    material = json.loads(raw)
    delivery = material.get('delivery') or {}
    if (material.get('format_version') != handoff['format_version']
        or material.get('backend') != definition['backend']
        or material['physical_world']['fingerprint'] != handoff['physical_world_fingerprint']
        or material['timeline'] != handoff['timeline']
        or (delivery and (delivery.get('index') != 0
            or delivery.get('previous_manifest_sha256') is not None
            or delivery.get('observation_interval_seconds') != definition['simulation']['observation_interval_seconds']))):
        raise ValueError('selected initial materials do not match the generated execution inputs')
    return {**handoff, 'manifest_sha256': sha256(raw).hexdigest(),
            'sequence_sha256': material['sequence_sha256'],
            'realization_count': material['realization_count'],
            'boundary_count': material['binary']['boundary_count']}


def _read_adoption_proposal(output, request):
    from hashlib import sha256
    root = Path(output) / '.control/revision-proposals'
    path = Path(output) / request['proposal']
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('adoption proposal must be an experiment-owned file')
    data = path.read_bytes()
    if sha256(data).hexdigest() != request['proposal_sha256']:
        raise ValueError('adoption proposal changed after submission')
    value = json.loads(data)
    return value['candidates'], value['expected']


class _MaterialCatalog:
    def __init__(self, output):
        self.output = Path(output)
        self._rows = {}
        self._offset = 0
        self._lock = RLock()
        self._refresh_lock = RLock()
        self._version = 0
        self.refresh()
        selection = self.output / 'results/compiler/selection.json'
        if selection.exists():
            self._rows.update({int(k): v for k, v in json.loads(selection.read_text())['selected'].items()})

    def refresh(self):
        # Serialize the append cursor separately; immutable-file I/O must not
        # hold the lock used by selection and pending registration.
        with self._refresh_lock:
            path = self.output / 'results/compiler/materials.jsonl'
            if not path.exists():
                return
            rows = []
            with path.open() as stream:
                stream.seek(self._offset)
                while line := stream.readline():
                    rows.append(json.loads(line))
                offset = stream.tell()
            with self._lock:
                for row in rows:
                    if row['index'] not in self._rows:
                        self._rows[row['index']] = row
                        self._version += 1
                self._offset = offset

    def selected(self):
        self.refresh()
        with self._lock:
            return [dict(self._rows[k]) for k in sorted(self._rows)]

    def get(self, index):
        with self._lock:
            if index in self._rows:
                return dict(self._rows[index])
        self.refresh()
        with self._lock:
            row = self._rows.get(index)
            return None if row is None else dict(row)

    def register(self, index, runner):
        """Read a selected immutable header, then register only if still selected."""
        from skyseed.backend.execution_materials.codec import _read_delivery_contract
        if not runner.status()['accepts_batch']:
            return False
        row = self.get(index)
        if row is None:
            return False
        contract = _read_delivery_contract(self.output / row['manifest'])
        with self._lock:
            if self._rows.get(index) != row or not runner.status()['accepts_batch']:
                return False
            return runner._append_prevalidated(contract)

    def adopt(self, candidates, expected, *, runner=None):
        """Validate immutable files without delivery locks; recheck before selection.

        The sole runtime decision consumer (or exclusive offline runtime claim)
        owns adoption. Runner's short decision arbitrates actual preparation.
        """
        from skyseed.backend.execution_materials.codec import _read_delivery_contract
        import os
        from uuid import uuid4
        self.refresh()
        first = candidates[0]['index']
        with self._lock:
            selected = dict(self._rows)
            version = self._version
        actual = [selected[k]['manifest'] for k in range(max(0, first - 1), max(selected) + 1)]
        changed = {'adopted': False, 'reason': 'selected predecessor or suffix changed', 'next_index': first}
        if actual != expected:
            return changed
        if [r['index'] for r in candidates] != list(range(first, max(selected) + 1)):
            raise ValueError('revision must replace the complete dependent suffix')
        contracts = [_read_delivery_contract(self.output / row['manifest']) for row in candidates]
        predecessor = None if first == 0 else _read_delivery_contract(self.output / selected[first - 1]['manifest'])
        for row, contract in zip(candidates, contracts):
            original = _read_delivery_contract(self.output / selected[row['index']]['manifest'])
            if (contract['delivery']['index'] != row['index']
                or any(contract[k] != original[k] for k in ('backend', 'world', 'timeline'))
                or contract['delivery']['observation_interval_seconds'] != original['delivery']['observation_interval_seconds']):
                raise ValueError('candidate changes the delivery input contract')
            expected_seal = None if predecessor is None else predecessor['manifest_sha256']
            if contract['delivery']['previous_manifest_sha256'] != expected_seal:
                raise ValueError('candidate predecessor seal differs')
            predecessor = contract
        if first == 0:
            definition = json.loads((self.output / 'results/compiler/execution.json').read_text())
            _verify_initial_reference(definition, contracts[0]['path'])
        selected.update({r['index']: dict(r) for r in candidates})
        destination = self.output / 'results/compiler/selection.json'
        temporary = destination.with_name('.selection-' + uuid4().hex + '.json')
        committed = False
        try:
            temporary.write_text(json.dumps({'selected': {str(k): v for k, v in selected.items()},
                'authority': 'runtime' if runner else 'computation', 'revision_id': candidates[0]['version']}))
            self.refresh()
            with self._lock:
                if self._version != version:
                    return changed
                def select():
                    nonlocal committed
                    os.replace(temporary, destination)
                    committed = True
                    self._rows = selected
                    self._version += 1
                if runner is None:
                    select()
                    return {'adopted': True}
                return runner.select_future_materials(contracts, select)
        finally:
            # Once renamed, selection is authoritative; there is no remaining
            # candidate file to clean up (or fallible post-selection operation).
            if not committed:
                import sys
                primary = sys.exc_info()[1]
                try:
                    temporary.unlink(missing_ok=True)
                except OSError as error:
                    if primary is None:
                        raise
                    primary.add_note(f'candidate selection cleanup: {error}')


class _AdoptionCancelled(Exception):
    """Local cancellation before the first possible selection/admission effect."""


def _adopt_revision(output, candidates, expected, *, cancel=None):
    """Offline adoption and runtime handoff share the existing exclusive claim."""
    from contextlib import closing
    from .interaction import RequestReceipt, InteractionUnavailable, TERMINAL
    from hashlib import sha256
    from time import sleep
    def check_cancel():
        if cancel is not None and cancel.is_set():
            raise _AdoptionCancelled()

    def wait_before_attempt():
        if cancel is None:
            sleep(.1)
        else:
            cancel.wait(.1)
        check_cancel()

    identity = 'adopt-' + sha256(candidates[0]['manifest'].encode()).hexdigest()[:24]
    with closing(InteractionClient.from_output(output)) as client:
        # Runtime can hold the claim before publishing its first session. Wait
        # through that handoff; never infer offline authority from absent state.
        while True:
            check_cancel()
            try:
                client._call('status')
                check_cancel()
                try:
                    with claim_runtime(output):
                        check_cancel()
                        # From this call onward selection may have occurred.
                        return _MaterialCatalog(output).adopt(candidates, expected)
                except RuntimeError as error:
                    if 'runtime is active' not in str(error):
                        raise
                state = client.state('runtime')
                if state['available']:
                    session = state['session_id']
                    break
            except InteractionUnavailable:
                pass
            wait_before_attempt()
        receipt = RequestReceipt(identity, 'runtime', session, client.experiment_id, 0)
        # The request carries a sealed reference, not a bulk candidate catalog.
        proposal = Path(output) / '.control/revision-proposals' / (identity + '.json')
        proposal.parent.mkdir(exist_ok=True)
        data = json.dumps({'candidates': candidates, 'expected': expected}, sort_keys=True).encode()
        proposal.write_bytes(data)
        reference = {'proposal': str(proposal.relative_to(output)), 'proposal_sha256': sha256(data).hexdigest()}
        submitted = attempted = False
        while True:
            if not attempted:
                check_cancel()
            try:
                result = client.result(receipt)
                if result is None and not submitted:
                    state = client.state('runtime')
                    if state.get('session_id') != session or state.get('live') is False:
                        raise RuntimeError('runtime session ended before adoption admission')
                    if not attempted:
                        check_cancel()
                    # Set before the call: a lost acknowledgement can mean the
                    # request was accepted, even while submitted is still False.
                    attempted = True
                    receipt = client.submit('runtime', 'adopt', request_id=identity, session_id=session,
                                            **reference)
                    submitted = True
                elif result is not None and result['status'] in TERMINAL:
                    break
            except InteractionUnavailable:
                # Completion may be indeterminate. Do not restore the old head
                # and publish an incompatible suffix while adoption is unknown.
                pass
            if attempted:
                sleep(.1)
            else:
                wait_before_attempt()
        if result['status'] != 'SUCCEEDED':
            raise RuntimeError(result.get('error', 'runtime adoption did not complete'))
        return result['result']
