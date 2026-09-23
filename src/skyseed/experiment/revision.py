"""Recompute a dependent future suffix using framework continuation values.

This runs on Producer's existing calculation thread. Researcher Model instances
remain live and are not copied or rolled back. Candidate evidence is never mixed
into the original append-only history.
"""
from datetime import datetime
from pathlib import Path
from .materials import _MaterialCatalog, _adopt_revision, _AdoptionCancelled
from skyseed.backend.execution_materials.sealing import _RunnerArtifactWriter



def _run_revision(producer, output, identity):
    status = producer._revisions[identity]
    target = None if status['requested_position'] is None else datetime.fromisoformat(status['requested_position'])
    original_cursor = producer._capture_continuation()
    original_head = producer._writer_checkpoint
    adopted = False
    start_index = 0
    attempts = []
    try:
        while not producer._stop.is_set():
            selected = _MaterialCatalog(output).selected()
            eligible = [row for row in selected if row['index'] >= start_index
                        and (target is None or datetime.fromisoformat(row['timeline']['start']) >= target)
                        and row['index'] - 1 in producer._window_checkpoints]
            if not eligible:
                status.update(status='REJECTED', reason='no eligible complete future range remains', attempts=attempts)
                return
            first = eligible[0]['index']
            suffix = [r for r in selected if r['index'] >= first]
            predecessor, writer_checkpoint = producer._window_checkpoints[first - 1]
            # All descendants are recalculated after restoring the predecessor
            # selected by the authority. Never retime a losing candidate suffix.
            producer._restore_continuation(predecessor)
            producer._formal_exhausted = False
            producer._finished = False
            root = Path(output) / 'results/revisions' / identity / f'attempt-{len(attempts):03d}'
            candidates, checkpoints = [], {}
            writer = None
            with producer._candidate_evidence(root, status.setdefault('evidence_attempts', [])) as evidence:
                try:
                    for old in suffix:
                        writer = _RunnerArtifactWriter(root / f'window-{old["index"]:03d}',
                            continuation=None if writer_checkpoint is None else writer_checkpoint.restore(), final=False,
                            dataplane_program=producer._dataplane_program if writer_checkpoint is None else None,
                            forwarding_backend=producer._forwarding_backend,
                            execution_observer=producer._execution_observer())
                        end = datetime.fromisoformat(old['timeline']['end'])
                        # A checkpoint may precede a subsequently extended Timeline.
                        if end > producer._timeline.end_time:
                            producer._formal_exhausted = True
                            producer.extend(end)
                        while (result := producer.produce_next_realization(delivery_end=end)) is not None:
                            writer.append(result)
                        manifest = writer.finalize()
                        metadata = writer.sealed_metadata()
                        writer_checkpoint = writer.continuation()
                        checkpoints[old['index']] = (producer._capture_continuation(), writer_checkpoint)
                        candidates.append({**old, 'manifest': str(manifest.relative_to(output)),
                            'version': identity, 'predecessor_version': (candidates[-1]['version'] if candidates else
                                selected[first-1].get('version', 'original') if first else None),
                            'evidence': str((root / 'evidence').relative_to(output)),
                            'timeline': metadata['manifest']['timeline']})
                        writer.close()
                        writer = None
                    producer.flush_evidence()
                    from .output.history import _DOMAIN_HISTORY_STREAM_NAMES
                    summaries = {name: evidence.history._evidence_writer.sealed_summary(root / 'evidence' / relative)
                                 for name, relative in _DOMAIN_HISTORY_STREAM_NAMES.items()}
                    physical_manifest = evidence.publish_physical()
                    for row in candidates:
                        row['evidence_streams'] = summaries
                        row['physical_link_source'] = None if evidence.physical is None else str(evidence.physical.path.relative_to(output))
                        row['physical_links'] = physical_manifest
                    expected = [r['manifest'] for r in selected if r['index'] >= first - 1]
                    result = _adopt_revision(output, candidates, expected, cancel=producer._stop)
                    if result['adopted']:
                        # Adoption is authoritative even if evidence cleanup or
                        # later publication fails. Retain its compatible head now.
                        adopted = True
                        producer._window_checkpoints.update(checkpoints)
                        producer._writer_checkpoint = writer_checkpoint
                        status.update(adopted=True, actual_position=candidates[0]['timeline']['start'],
                                      selection={'path': 'results/compiler/selection.json', 'version': identity,
                                                 'candidate_root': str(root.relative_to(output)),
                                                 'first_index': first, 'windows': len(candidates)})
                finally:
                    import sys
                    primary = sys.exc_info()[1]
                    try:
                        if writer is not None:
                            writer.close()
                    except BaseException as error:
                        if primary is None:
                            raise
                        primary.add_note(f'candidate writer cleanup: {error}')
            if result['adopted']:
                # Re-publish the selected chain while retaining immutable original
                # evidence and candidate evidence under their own file roots.
                if producer._revision_published is not None:
                    producer._revision_published()
                status.update(status='SUCCEEDED' , actual_position=candidates[0]['timeline']['start'],
                              attempts=attempts)
                return
            attempts.append({'from_index': first, **result})
            start_index = result.get('next_index')
            status.update(status='DEFERRED', attempts=attempts)
            if start_index is None:
                status.update(status='REJECTED', reason=result['reason'])
                return
        status.update(status='CANCELLED', reason='calculation stopped', attempts=attempts)
    except _AdoptionCancelled:
        status.update(status='CANCELLED', reason='calculation stopped before adoption', attempts=attempts)
    except Exception as error:
        status.update(status='FAILED', error=f'{type(error).__name__}: {error}', attempts=attempts)
        if getattr(error, '__notes__', ()):
            status['error_notes'] = list(error.__notes__)
    finally:
        if not adopted:
            producer._restore_continuation(original_cursor)
            producer._writer_checkpoint = original_head
