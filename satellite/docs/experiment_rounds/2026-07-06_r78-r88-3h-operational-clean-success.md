# r78-r88: 3H Operational Clean Success And Next Optimization Line

Date: 2026-07-06
Status: validated; close this major round before structural optimization

## Goal

- Validate the frozen-input horizon maintenance contract from r72-r77 in a
  fresh 3H/240s `operational_clean` run.
- Keep the runtime path fail-closed: no fallback, full reload, full scan, live
  full map dump/diff, future host activation, or hidden reconcile path.
- Record the success point and next optimization route before starting larger
  maintenance/process and qdisc write-path changes.

## Outcome

- r88 completed the 3H run:
  `satellite/output/validation_profiles/s4-full-h3-240s-runtime-20260706-r88`
- Runtime progress reached `75/75` runtime epochs:
  - H0: `25`
  - H1: `25`
  - H2: `25`
- H1 and H2 maintenance both published ready artifacts and were consumed by the
  runtime boundary before entering the next horizon.
- Runtime forbidden-work counters stayed clean for the core path:
  - fallback: `0`
  - Linux G/Z fallback commands: `0`
  - runtime full attach/full reload/live full map dump or diff: not observed
  - runtime BPF attach/map compile/live role read in steady epochs: `0`
- Post-run runtime semantics succeeded. A compose network residue was cleaned
  after the run and the cleanup path was fixed to handle stale endpoints.

## Findings

- The horizon-maintenance freeze contract is working:
  - worker preload consumed `queued_horizon_maintenance_frozen_manifest`
  - worker preload did not repeat interface discovery/logical binding
  - worker preload did not repeat attach or live clsact guard work
  - active generation changed only at the runtime boundary
- MAP write/delete is no longer the dominant cost:
  - steady BPF map write averaged about `0.013s` per epoch
  - H1 inactive preload records summed about `9.29s`
  - H2 inactive preload records summed about `9.21s`
  - retired-generation cleanup map delete/write averaged about `0.090s`
- Runtime epoch wall time is real hot-path time, not startup time:
  - steady epoch wall averaged about `25.46s`
  - network update averaged about `17.10s`
  - host apply averaged about `13.30s`
  - qdisc wall averaged about `12.48s`
  - post-apply scheduler blocking averaged about `4.42s`
  - unknown blocking averaged about `3.12s`
- H2 preparation was slower than H1 because the large costs moved to physical
  and attach-capability preparation:
  - H2 host additive prepare: about `58.67s`
  - H2 attach capability prepare: about `159.76s`
  - H2 frozen compiled inputs: about `63.93s`
- r88 was a bounded 3H run, so it prepared H1 and H2 only. It did not prepare
  H3, which is correct for this run shape but means H2->H3 successor
  preparation still needs a later 4H or equivalent validation.
- Retired-generation cleanup completed normally:
  - completed records: `68`
  - queued remaining: `0`
  - blocked records: `1`, for `H0:generation-0` still referenced as an overlay
    base. This is a protection record, not a cleanup stall.

## Changes In This Round

- Completed the handoff-record alignment needed for H1/H2 boundary consumption.
- Aligned BPF generation capacity/slot metadata with the previous/current/next
  triple-buffer model.
- Fixed several report and artifact contract mismatches around maintenance
  readiness, frozen compiled inputs, manifest-only map writes, stale compose
  endpoint cleanup, and active-readiness handoff records.
- Added focused tests for horizon maintenance readiness, frozen input use,
  prepared attach proof use, BPF manifest-only writes/deletes, compose residue
  cleanup, and related runtime defaults.

## Lessons

- Do not spend long runs checking whether a one-line alignment fix works. Sweep
  adjacent contracts first: producer, consumer, compact artifact, sidecar/full
  artifact, audit/report, and focused tests.
- A guard added to protect the clean path can become the failure if it reads the
  wrong lifecycle set. Live checks must be scoped to current or pending work,
  not historical sets after normal consumption/cleanup has changed them.
- When a failure looks like an old failure returning, compare with the previous
  round notes before patching. Repeated symptoms may share one contract gap even
  if they surface in different artifacts.
- Monitor stable long phases at lower frequency. Increase monitoring only near
  first use of new paths, horizon boundaries, ready consumption, fail-closed
  decisions, and final publication.

## Next Work

1. Qdisc batch executor for the runtime hot path.
   - Current qdisc updates are semantically valid but expensive.
   - Preserve the rule that qdisc changes apply only at the current runtime
     boundary.
   - Convert per-interface `tc qdisc replace/del` command churn into a
     manifest/batch executor where possible.
   - Keep partial failures fail-closed and report command count separately from
     sub-update count.

2. Maintenance module processization and parallelization.
   - Move horizon maintenance from background-thread style orchestration toward
     a standalone maintenance process/worker.
   - Run future physical host additive prepare, BPF attach capability prepare,
     inactive generation map preload, and retired-generation cleanup as
     independent subtasks where their contracts allow it.
   - Keep active/visible state changes in the runtime boundary only.

3. Attach-capability prepare semantic cleanup.
   - Treat remaining wide interface discovery/logical binding inside
     attach-capability preparation as a semantic debt item, not a mere
     optimization.
   - Prefer prepared host plans and frozen attach/role inputs over live
     discovery wherever the data is already knowable before the boundary.

## Watch Next

- The next conversation should start from this run note and the session-check
  command, not from chat memory.
- r88 remains the current success baseline for 3H/240s operational clean.
- For qdisc work, the proving metric is lower
  `parameter_qdisc_wall_seconds`/host apply wall without changing runtime
  semantics or hiding failures.
- For maintenance-process work, the proving metric is transparent subtask wall
  time and ready/blocked/failed artifacts, not just an earlier ready timestamp.
