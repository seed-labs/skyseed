"""Etcd control consumers. Public submissions never call local primitives directly."""
from contextlib import contextmanager
from datetime import datetime
import argparse
import json
from pathlib import Path
from threading import Event, Thread
from time import monotonic
from skyseed.shared._console import _console
from .interaction import InteractionClient, InteractionUnavailable, _OwnerPublisher, TERMINAL

_ControlUnavailable = InteractionUnavailable


def _request(output, owner, command, **arguments):
    client = InteractionClient.from_output(output)
    try:
        if command == 'status':
            state = client.state(owner)
            if not state['available']:
                raise _ControlUnavailable(f'{owner} state unavailable or stale')
            return state
        if command in {'runtime_claim', 'runtime_release'}:
            # Shutdown/failure records outlive computation. Runtime
            # holds the output's exclusive runtime claim before this handshake.
            session = client.state('computation')
            closing = client._get('coordination/shutdown')
            failure = client._get('coordination/computation-failure')
            if command == 'runtime_claim' and failure:
                raise RuntimeError(f'retained computation failed: {failure["value"]}')
            return {**session, 'shutdown_requested': bool(closing)}
        return client.submit(owner, command, **arguments).artifact()

    finally:
        client.close()


def _position(state, owner):
    return (state.get('runner') or {}).get('active_frontier') if owner == 'runtime' else state.get('completed_end')


@contextmanager
def _serve_control(output, owner, dispatch, *, initialized=None, observe_runtime=None):
    """One serial decision consumer per real owner; slow work stays in owners.

    Poll only live pending requests and bounded status, never request history.
    Losing etcd stops new decisions, not locally admitted work or cleanup.
    """
    if owner == 'runtime':
        check = InteractionClient.from_output(output)
        check._call('status')
        check.close()
    stop, ready = Event(), Event()
    failures = []
    def consume():
        client = publisher = None
        running = {}
        last_invocation = None
        retiring = None
        next_runtime_read = 0.
        def finish_request(target, key, row, state, *, closed=False, replacement=None):
            """Settle only facts known by this owner; never replay an effect."""
            command = row['intent']['command']
            result = row.get('result') or {}
            status, facts = None, {}
            if command in {'cancel-runtime', 'cancel-computation', 'shutdown'}:
                if closed:
                    outcome = state.get('outcome')
                    failed = (state.get('error') or state.get('execution_error')
                        or state.get('state') == 'FAILED'
                        or (state.get('runner') or {}).get('lifecycle_state') == 'teardown-failed'
                        or outcome and outcome.get('status') != 'completed')
                    if failed or outcome is not None:
                        status = 'FAILED' if failed else 'SUCCEEDED'
                        facts = {'result': {'reason': 'owner closed', 'state': state}}
                elif replacement is not None:
                    status, facts = 'EXPIRED', {'reason': 'target runtime was reset'}
            elif 'result' not in row:
                # Dispatch may have taken effect before raising an unavailable
                # error. Session loss does not resolve that uncertainty.
                return False
            elif command == 'reset':
                if replacement is not None:
                    status, facts = 'SUCCEEDED', {'result': {**result,
                        'reason': 'runtime invocation replaced', 'invocation_id': replacement}}
                elif closed:
                    status = 'FAILED' if state.get('error') or (state.get('outcome') or {}).get('status') not in {None, 'completed'} else 'EXPIRED'
                    facts = {'result': {**result, 'reason': 'owner closed before reset', 'state': state}}
            elif command == 'revise':
                revision = dispatch({'command': 'revision-status', 'revision_id': result['revision_id']})
                if revision.get('status') in TERMINAL:
                    status, facts = revision['status'], {'result': revision,
                        'actual_position': revision.get('actual_position')}
                elif not closed and revision != row.get('progress'):
                    running[key] = target.update(key, row,
                        'DEFERRED' if revision.get('status') == 'DEFERRED' else 'RUNNING', progress=revision)
            else:
                runner = (result if replacement is not None else state).get('runner') or {}
                complete = True
                if command == 'continue':
                    complete = bool(state.get('completed_end') and
                        datetime.fromisoformat(state['completed_end']) >= datetime.fromisoformat(result['requested_end']))
                elif command == 'pause':
                    complete = runner.get('paused', False)
                    scheduled = runner.get('pause_scheduled_at')
                    if not closed and replacement is None and scheduled is not None and scheduled != row.get('scheduled_position'):
                        running[key] = row = target.update(key, row, 'RUNNING', scheduled_position=scheduled)
                elif command == 'resume':
                    complete = not runner.get('pause_requested', True) and not runner.get('paused', True)
                if complete:
                    status, facts = 'SUCCEEDED', {'actual_position': row.get('actual_position') if replacement is not None else _position(state, owner)}
                elif closed or replacement is not None or state.get('state') in {'CLOSED', 'CANCELLED', 'FAILED'}:
                    status, facts = 'EXPIRED', {'reason': 'target session ended before completion'}
            if status is None:
                return False
            target.update(key, row, status, **facts)
            if command == 'revise':
                dispatch({'command': 'revision-result-stored', 'revision_id': result['revision_id']})
            return True

        def finish_retiring():
            nonlocal retiring
            if retiring is None:
                return
            previous, admitted, invocation, state = retiring
            for key, row in admitted.items():
                finish_request(previous, key, row, state, replacement=invocation)
            for pending in previous.pending():
                if pending['value'] in admitted:
                    continue  # An unresolved admitted effect remains indeterminate.
                row = client._get(pending['value'])['value']
                previous.update(pending['value'], row, 'EXPIRED', error='target runtime was reset')
            retiring = None
        try:
            while not stop.is_set():
                if initialized is not None and not initialized.is_set():
                    stop.wait(.05)
                    continue
                try:
                    if client is None:
                        client = InteractionClient.from_output(output)
                        publisher = _OwnerPublisher(client, owner)
                    if observe_runtime is not None and monotonic() >= next_runtime_read:
                        next_runtime_read = monotonic() + 1.
                        try:
                            observe_runtime(client.state('runtime'))
                        except InteractionUnavailable:
                            pass  # Missing/stale facts can only postpone local pruning.
                    state = dispatch({'command': 'status'})
                    shutdown = client._get('coordination/shutdown')
                    failure = client._get('coordination/computation-failure') if owner == 'runtime' else None
                    if owner == 'computation' and shutdown and not state.get('shutdown_requested'):
                        state = dispatch({'command': 'shutdown'})
                    elif owner == 'runtime' and (shutdown or failure) and not state.get('cancel_requested'):
                        state = dispatch({'command': 'cancel-runtime'})
                    invocation = state.get('invocation_id') if owner == 'runtime' else None
                    if last_invocation is not None and invocation != last_invocation:
                        finish_retiring()
                        retiring = publisher, running, invocation, state
                        running = {}
                        publisher = _OwnerPublisher(client, owner)
                    last_invocation = invocation
                    publisher.publish(state)
                    finish_retiring()  # new publication fences old-session admission
                    ready.set()
                    for pending in publisher.pending():
                        key = pending['value']
                        if key in running:
                            continue
                        row = client._get(key)['value']
                        if row['status'] in TERMINAL:
                            continue
                        intent = row['intent']
                        command = intent['command']
                        position, target = _position(state, owner), intent['requested_position']
                        try:
                            if (owner == 'computation' and state.get('producer_ready') is False
                                    and command in {'continue', 'revise', 'parameters'}):
                                publisher.update(key, row, 'DEFERRED', reason='waiting for computation initialization')
                                continue
                            if owner == 'runtime' and state.get('runner') is None and command in {'adopt', 'parameters'}:
                                publisher.update(key, row, 'DEFERRED', reason='waiting for Runner construction')
                                continue
                            if target is not None:
                                target_time = datetime.fromisoformat(target)
                                if target_time.tzinfo is None:
                                    raise ValueError('scheduled position must have a timezone')
                                if command not in {'pause', 'revise'} and (position is None or datetime.fromisoformat(position) < target_time):
                                    publisher.update(key, row, 'DEFERRED', scheduled_position=target,
                                                     reason='waiting for owner boundary')
                                    continue
                            # Persist admission before side effects. No claim of
                            # exactly-once effects after process failure.
                            row = publisher.update(key, row, 'RUNNING', scheduled_position=target or position)
                            if row['status'] in TERMINAL:
                                continue
                            running[key] = row
                            result = dispatch({'command': command, **intent['arguments'], 'at': target, 'request_id': intent['request_id'], 'target_invocation': last_invocation})
                            scheduled = ((result.get('runner') or {}).get('pause_scheduled_at')
                                         if command == 'pause' else target or position)
                            admitted = row
                            row = {**row, 'result': result, 'actual_position': position,
                                   'scheduled_position': scheduled}
                            running[key] = row  # retain effects even if acknowledgement is lost
                            if command in {'pause', 'resume'}:
                                for older_key, older in tuple(running.items()):
                                    if older_key != key and older['intent']['command'] in {'pause', 'resume'}:
                                        running[older_key] = {**older, 'superseded_by': intent['request_id']}
                            running[key] = publisher.update(key, admitted, 'RUNNING', result=result,
                                actual_position=position, scheduled_position=scheduled)

                        except InteractionUnavailable:
                            if command in {'parameters', 'shutdown'} and 'result' not in running.get(key, {}):
                                running.pop(key, None)  # These local primitives are explicitly idempotent.
                            raise
                        except (ValueError, TypeError, KeyError, OverflowError, RuntimeError) as error:
                            running.pop(key, None)
                            publisher.update(key, row, 'REJECTED', error=f'{type(error).__name__}: {error}')
                        except OSError as error:
                            running.pop(key, None)
                            publisher.update(key, row, 'FAILED', error=f'{type(error).__name__}: {error}')
                    for key, row in tuple(running.items()):
                        if row.get('superseded_by'):
                            publisher.update(key, row, 'EXPIRED', error='superseded by a later pause/resume request')
                            del running[key]
                            continue
                        if 'result' not in row:
                            continue  # An interrupted dispatch has indeterminate effects.
                        now = dispatch({'command': 'status'})
                        if finish_request(publisher, key, row, now):
                            del running[key]
                except InteractionUnavailable:
                    pass
                except BaseException as error:
                    failures.append(error)
                    # Retain ownership until the real context closes. A failed
                    # consumer is not evidence of drained local resources.
                    stop.wait()
                    break
                stop.wait(.1)
        finally:
            if publisher is not None:
                try:
                    final = dispatch({'command': 'status'})
                    publisher.publish(final, live=False)  # fence new admission before final drain
                    finish_retiring()
                    for key, row in running.items():
                        finish_request(publisher, key, row, final, closed=True)
                    for pending in publisher.pending():
                        key = pending['value']
                        if key in running:
                            continue
                        row = client._get(key)['value']
                        if (row['intent']['command'] in {'cancel-runtime', 'cancel-computation', 'shutdown'}
                                and row['intent']['requested_position'] is None):
                            finish_request(publisher, key, row, final, closed=True)
                        else:
                            publisher.update(key, row, 'EXPIRED', reason='owner closed before admission')
                except InteractionUnavailable:
                    pass  # Last publication remains stale; closure is indeterminate.
                client.close()
    thread = Thread(target=consume, name=f'skyseed-{owner}-interaction')
    thread.start()
    try:
        if owner == 'runtime':
            while not ready.wait(.05):
                if failures:
                    raise failures[0]
        yield
    finally:
        stop.set()
        thread.join()
        if failures:
            _console(f'{owner} interaction consumer failed: {failures[0]}')


def _write_control_client(output):
    path = Path(output) / "experiment_control.py"
    path.write_text('''#!/usr/bin/env python3
"""Optional controls for this output's live experiment owners.

Continue preserves the original scene/Models and requires its computation
process. Pause holds the fully active network; container clocks/apps still run.
Reset cleans one runtime invocation, then replays retained materials.
Submissions print receipt JSON, not success of the effect. Save it and use
result '<receipt JSON>'; shutdown returns one receipt per available owner.
status includes retained closed outcomes. Failed cleanup remains a failure.
Etcd has a separate lifetime: python -m skyseed.experiment.interaction_service
stop <output> after owners close. See this output's README.md for connection help.
"""
from pathlib import Path
from skyseed.experiment._control import control_main

# Editable defaults. Explicit CLI arguments override these values.
ADDITIONAL_SECONDS = 300.0   # Used by continue unless TARGET_END is set.
TARGET_END = None           # Absolute ISO UTC endpoint, e.g. 2026-01-01T00:15:00+00:00.
BATCH_SECONDS = 300.0       # Delivery duration; None inherits the session setting.

if __name__ == "__main__":
    raise SystemExit(control_main(Path(__file__).resolve().parent,
        additional_seconds=ADDITIONAL_SECONDS, target_end=TARGET_END,
        batch_seconds=BATCH_SECONDS))
''', encoding="utf-8")
    path.chmod(0o755)


def _status_summary(computation, runtime):
    """Describe existing owner snapshots; an absent endpoint proves no completion."""
    for label, state in (('Calculation', computation), ('Runtime', runtime)):
        if state.get('live') is False:
            outcome = state.get('outcome')
            _console(f"{label} owner explicitly closed; " +
                     (f"recorded outcome: {json.dumps(outcome, sort_keys=True)}."
                      if outcome is not None else "outcome missing; completion remains indeterminate."))
    calculation_closed = computation.get('live') is False
    runtime_closed = runtime.get('live') is False
    calculation_available = not calculation_closed and computation.get('available') is not False and bool(computation)
    if calculation_available:
        _console(f"Calculation: {computation.get('state', 'unknown')}; requested through "
                 f"{computation.get('requested_end', 'unknown')}; range ready through "
                 f"{computation.get('completed_end') or 'none yet'}. IDLE means calculation is waiting, not runtime completion.")
    elif not calculation_closed:
        _console("Calculation observation unavailable or stale; closure is not confirmed by status.")
    if runtime_closed:
        _console("Runtime closure alone does not certify successful cleanup; inspect the outcome. "
                 "Independently deployed containers require cleanup.sh.")
    elif runtime.get('available') is False or not runtime:
        _console("Runtime observation unavailable or stale; cleanup is not confirmed by status. "
                 "Check the original execution terminal; use run.sh after deployment to start execution.")
    else:
        runner = runtime.get('runner') or {}
        readiness = runtime.get('initial_readiness') or {}
        _console(f"Runtime: {runner.get('lifecycle_state', 'awaiting preparation')}; active through "
                 f"{runner.get('active_frontier') or 'none yet'}; requested through "
                 f"{computation.get('requested_end') or 'unknown (calculation endpoint unavailable)'}; "
                 f"available through {runner.get('available_end') or 'not loaded'}. "
                 f"User preparation: {readiness.get('status', 'not active')}.")
        if runtime.get('cancel_requested') or runner.get('cancel_requested'):
            _console("Runtime stop requested; owned work is draining. Watch the execution terminal for runtime cleanup, then Tool results.")
        elif runner.get('paused') or runner.get('pause_requested'):
            _console("Runtime pause is active or requested; use resume to release it. Missing materials may still hold progression.")
        elif runner.get('waiting_for_material') or 'material' in runner.get('wait_reasons', ()):
            _console("Runtime is waiting for execution materials; the network remains active. "
                     + ("Use continue to request another range, or cancel-runtime to stop."
                        if computation.get('state') == 'IDLE' else "Watch calculation progress, or use cancel-runtime to stop."))
        else:
            _console("Watch the execution terminal for preparation/progression; use pause or cancel-runtime as needed.")
        if runner.get('maintenance_error'):
            _console(f"Runtime maintenance error: {runner['maintenance_error']}")
        if readiness.get('error'):
            _console(f"User preparation error: {readiness['error']}")
    for key in ('delivery_error', 'error'):
        if runtime.get(key):
            _console(f"Runtime error: {runtime[key]}")
    for key in ('error', 'execution_error', 'recording_failure'):
        if computation.get(key):
            _console(f"Calculation {key}: {computation[key]}")


def control_main(output, *, additional_seconds=300., target_end=None, batch_seconds=None, argv=None):
    parser = argparse.ArgumentParser(
        description='Submit experiment requests; receipts are not execution completion',
        epilog='Save submission JSON and pass it to result. A zero CLI exit is not effect success. '
               'shutdown returns an owner-to-receipt map; query each receipt. '
               'See README.md for host/container clients and explicit Etcd stop/restart.')
    commands = parser.add_subparsers(dest='command', required=True)
    descriptions = {
        'pause': 'Hold updates after a completed boundary; applications keep running',
        'resume': 'Release the user pause; missing materials may still hold execution',
        'reset': 'Drain this invocation and replay retained materials with a fresh Runner',
        'cancel-runtime': 'Request Runner/Traffic drainage; inspect the final result',
        'cancel-computation': 'Drain retained calculation and presentation',
        'shutdown': 'Immediately request both available owners; returns a receipt map',
        'revise': 'Recalculate an eligible complete suffix; Runner keeps progressing',
    }
    for name, description in descriptions.items():
        command = commands.add_parser(name, help=description, description=description)
        command.add_argument('--at', help=(
            'aware scene timestamp: select a complete window starting at/after it; submit now'
            if name == 'revise' else 'aware scene timestamp; default earliest valid boundary (shutdown is immediate)'))
        command.add_argument('--request-id', help='deduplicate identical intent within the target owner session')
    commands.add_parser('status', help='Read owner observations, freshness and retained final outcomes')
    result = commands.add_parser('result', help='Read progress/terminal outcome for one saved receipt')
    result.add_argument('receipt', help='receipt JSON returned by submission')
    extend = commands.add_parser('continue', help='Extend live retained calculation; does not resume Runner')
    target = extend.add_mutually_exclusive_group()
    target.add_argument('--seconds', type=float)
    target.add_argument('--until')
    extend.add_argument('--batch-seconds', type=float, default=batch_seconds)
    extend.add_argument('--request-id')
    extend.add_argument('--at')
    parameter = commands.add_parser('parameters', help='Request supported settings at their effective boundary')
    parameter.add_argument('owner', choices=('computation', 'runtime'))
    parameter.add_argument('values', help='JSON of supported parameter values')
    parameter.add_argument('--at')
    parameter.add_argument('--request-id')
    args = parser.parse_args(argv)
    client = None
    try:
        client = InteractionClient.from_output(output)
        if args.command == 'status':
            value = {owner: client.state(owner) for owner in ('computation', 'runtime')}
            _status_summary(value['computation'], value['runtime'])
        elif args.command == 'result':
            value = client.result(json.loads(args.receipt))
        elif args.command == 'shutdown':
            if args.at is not None:
                raise ValueError('coordinated shutdown is immediate; schedule individual owner stops instead')
            value = {owner: receipt.artifact() for owner, receipt in client.shutdown(request_id=args.request_id).items()}
        elif args.command == 'continue':
            until = args.until or (target_end if args.seconds is None else None)
            value = client.submit('computation', 'continue', request_id=args.request_id, at=args.at,
                seconds=(args.seconds if args.seconds is not None else additional_seconds) if until is None else None,
                until=until, batch_seconds=args.batch_seconds).artifact()
        elif args.command == 'parameters':
            value = client.submit(args.owner, 'parameters', at=args.at, request_id=args.request_id,
                                  values=json.loads(args.values)).artifact()
        else:
            owner = 'computation' if args.command in {'cancel-computation', 'revise'} else 'runtime'
            value = client.submit(owner, args.command, at=args.at, request_id=args.request_id).artifact()
        print(json.dumps(value, indent=2), flush=True)
        return 0
    except (RuntimeError, ValueError) as error:
        parser.exit(1, f'{error}\n')
    finally:
        if client is not None:
            client.close()
