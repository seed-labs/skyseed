# r132: generation-zero H3 consumer

Date: 2026-07-11
Status: fixed; r133 gated

## Goal

- Validate the canonical 4H operational-clean lifecycle after removing repeated
  bootstrap discovery/clsact work and batching node-scoped worker preparation.

## Findings

- H0, H1, and H2 completed 25/25 epochs. The former r131 H2 epoch-22 drift
  location passed in 6.84s.
- Rolling workers published ready in 83.86-95.22s, including 29.39-29.84s BPF
  delta attach, 32.96-42.05s additive host prepare, and 3.18-4.59s for all 25
  inactive generation preloads. Forbidden-work counters were zero.
- H3 had 25/25 successfully written preload records, but its first physical
  generation is `0`. The consumer used `generation_id or -1`, so valid zero
  became `-1` and failed closed as `bpf_preloaded_generation_missing` after
  75 completed epochs.
- The roughly 302s `runtime_rollback:artifact_cleanup` was failure cleanup after
  the H3 exception, not a successful H-boundary operation. Final cleanup was
  clean.

## Changes

- Preloaded-record lookup and consumed-record removal now distinguish an absent
  generation field from explicit generation `0`.
- Retired cleanup protects active generation `0` with the same explicit-value
  rule, so the fix naturally covers later H4/H5 cycles.
- Maintenance readiness rejects any record that reports success without an
  actual inactive generation write.
- The companion gate consumes the real r132 H3 handoff and proves generation
  `0` first consumption, generation `1` successor consumption, record removal,
  and source-state advancement.

## Validation

- Focused lifecycle suite: 225/225.
- Full unittest discovery: 772/772.
- Companion failure-chain replay: 24/24, including r116-r130 and r132, real
  veth creation/capture/cleanup, and H0-H5 three-buffer cleanup semantics.
- Canonical r133 dry-run: continuous 4H, no epoch caps, operational-clean BPF.
- Preflight/session gate: clean/ready with zero scoped residue.
- `git diff --check`: passed.

## Rerun Gate

- Previous failed run/stage: r132, H3 first boundary consumer after 75/75
  H0-H2 epochs.
- Cheap check now catching it: `r132_generation_zero_h3_consumer` replays the
  real 25-record H3 handoff and consumes physical generations 0 then 1.
- Adjacent lifecycle states covered: producer wrote 25/25, first consumer,
  immediate successor, consumed-record retirement, active-zero cleanup guard,
  H0-H5 slot reuse, and partial-write failure publication.
- Command source and run id: canonical `rich_s4_operational_clean_bpf` profile;
  `r133-4h-generation-zero-consumer`.
- Forbidden work: Linux MPLS/LFIB, fallback, full reload/attach/scan, live
  dump/diff, hidden reconcile, runtime compile, future visibility, or early
  active flip.
- Remaining late-failure risk: real H3 execution after the corrected zero-value
  lookup; no known offline-detectable blocker remains.

## Watch Next

- Require H3 25/25 and 100/100 total epochs.
- Confirm zero forbidden-work counters, nonblocking ping observations, and clean
  teardown.
