# Experiment Round Ledger

This directory records Skyseed experiment work by round. Keep one short file per
round so future sessions can reconstruct what changed, why it changed, what was
validated, and what remains.

Use this ledger to spot repeated patterns, especially:

- accidental fallback to full regeneration or full reload
- skipped work whose downstream state was not advanced
- report/audit logic that still assumes an older path
- expensive work moved rather than removed or bounded
- temporary mitigations that were never replaced with flow-level fixes

## File Naming

Use:

```text
YYYY-MM-DD_<round-id>_<short-topic>.md
```

Examples:

```text
2026-07-04_r62_h1-preload-stabilization.md
2026-07-04_r63_3h-240s-rerun.md
```

## Round Template

```markdown
# <round id>: <short title>

Date: <YYYY-MM-DD>
Status: <planned | running | stopped | fixed | validated>

## Goal

- ...

## Findings

- ...

## Changes

- ...

## Validation

- ...

## Remaining

- ...

## Watch Next

- ...
```

Keep entries concise. Put exact commands, run ids, and artifact paths only when
they are needed to reproduce or compare the round. Prefer durable categories
over one-off details.

## Recent Entries

- `2026-07-06_r78-r88-3h-operational-clean-success.md`: r88 completed the
  fresh 3H/240s `operational_clean` run with H1/H2 ready artifacts consumed at
  runtime boundaries, fallback/full-reload/live-map forbidden work absent, and
  qdisc/host apply plus maintenance physical/attach preparation identified as
  the next optimization line. r82-r87 are summarized as lessons about adjacent
  contract sweeps and avoiding self-inflicted gates rather than as detailed
  attempt logs.
- `2026-07-05_r72-r77-horizon-maintenance-freeze.md`: r72-r77 horizon
  maintenance contract work; frozen compiled runtime inputs and prepared
  attach-capability proof remove repeated worker discovery/guard work, with
  focused tests passing and the next long run deferred to a fresh round.
- `2026-07-05_r71-operational-clean-horizon-maintenance.md`: r71
  operational-clean run reached one clean H0 runtime epoch, then stopped
  fail-closed before H1 queued preload because base compiled runtime inputs were
  missing; records the need for a horizon maintenance worker.
- `2026-07-04_r65-r66-clean-input-regeneration.md`: replaced corrupt prepared
  input lineage and regenerated clean r66 input.
- `2026-07-04_r64-bpf-clean-manifest-batch-queue.md`: clean manifest, planned
  overlay, batch map write, queued preload, and retired cleanup contract work.
- `2026-07-04_r62-h1-preload-stabilization.md`: H1 preload stabilization and
  earlier rolling-horizon failure evidence.
