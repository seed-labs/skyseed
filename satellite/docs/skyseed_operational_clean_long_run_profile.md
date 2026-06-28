# Skyseed Operational Clean Long-Run Profile

Updated: 2026-06-23.

## Status

Design prepared; not executed and not accepted yet.

The CLI now reserves `--runtime-mode operational_clean` for future long-run
measurement. This mode is intended to measure necessary steady-state network
updates, not the cost of the acceptance framework.

## Intended Use

Use `operational_clean` only after targeted validation proves:

- BPF attach reuse uses `reuse_attached_bpf_map_reload` when the attach plan is
  already covered;
- generation/readiness mismatch continues into strict prepare/reload instead of
  short-circuiting;
- ParameterEpoch lightweight validation skips full ground gateway sanity while
  default/final validation still keeps it;
- epoch wall-time attribution leaves less than 5% unaccounted wall.

Do not use this mode as long-run acceptance until those checks pass.

## Included Steady-State Work

- EventTimeline queue execution.
- RuntimeTransaction and ParameterUpdateBatch apply.
- BPF map reload/delta update where the attach plan is unchanged.
- `active_generation` switch.
- qdisc parameter update only when thresholds are crossed.
- Minimal strict counters: BPF miss/drop, fallback counters,
  active_generation state, transaction success/failure.
- Lightweight heartbeat and progress.
- First-failure packet-level diagnostics on failure.
- Final cleanup and final post-run preflight.

## Excluded From Per-Epoch Normal Path

- Full BPF attach/revalidation when the attach plan is unchanged.
- Full ground gateway sanity.
- Full ping matrix or full return-path probe set.
- Full ledger diff.
- Full qdisc delete/recreate when parameter state is unchanged.
- Full cleanup/reconcile after successful epochs.
- Repeated interface discovery unless veth/lifecycle state changed.
- Heavy artifact dumps every epoch.
- Correctness-mode validation watchers that create unrelated G/Z-scope traffic.

## Strict Invariants

`operational_clean` must not weaken dataplane correctness:

- no Linux G/Z/MPLS/LFIB fallback;
- no packet-keyed IP route fallback;
- G/Z FEC identity remains zone/class/anchor based;
- UT-specific reverse labels remain zone-local final delivery only;
- BPF miss/drop counters remain monitored;
- fallback counters remain zero;
- missing/unknown interface role remains fatal;
- G/Z-scope classifier miss remains fatal;
- MPLS shim LFIB/handoff miss remains fatal;
- first failure preserves packet-level diagnostics.

## Current Implementation Boundary

The switch is available through `--runtime-mode operational_clean`. The current
implementation uses the lightweight node-local apply posture for this mode and
keeps strict BPF-owned apply-contract checks intact. Before any long run, add a
small live validation proving the profile still emits the required counters and
fails closed on miss/drop or fallback.
