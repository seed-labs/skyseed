# r116: frozen endpoint binding handoff

Date: 2026-07-10
Status: r117 exposed binding-capture command error; fixed again

## Goal

- Validate process-owned H1 maintenance with direct delta attach and shared
  frozen compiler bindings in the canonical 4H operational-clean run.

## Findings

- r116 completed compose, H0 physical preparation, initial BPF preparation,
  and six H0 runtime epochs. H0 transitions were 0.91-6.81 seconds with zero
  Linux/fallback execution.
- H1 maintenance failed closed on
  `frozen_interface_mac_binding_missing:netns:ut2001:ua-u2001s0`.
- The first bug was source selection: the worker used attach-only details even
  though the base generation carried a broader frozen compiler endpoint cache.
- After correcting that source, actual frozen replay exposed two producer
  boundaries. Newly materialized additive interfaces need their kernel ifindex
  returned by the create operation, while H0-prepared interfaces that are first
  used in H1 need bindings preserved from H0 physical creation even when H0 did
  not attach them.

## Changes

- H0 veth creation returns deterministic MAC and kernel ifindex as one operation
  result. The ifindex read is combined with the existing MAC-set `nsenter`, so
  this does not add one process per prepared interface.
- H0 prewarm returns a complete frozen binding cache and initial BPF compilation
  consumes it strictly; a missing binding now fails before runtime.
- Additive worker creation returns the same binding record shape. The worker
  merges it into the shared 25-generation cache before the first compile.
- Frozen base cache, attach operation details, and additive creation results are
  unioned with conflict checks. No live interface lookup or fallback was added.

## Validation

- 566 focused and adjacent regression tests pass.
- The companion gate compiles all 25 real r116 H1 frozen work items in 0.98s
  while live ifindex/MAC functions are patched to fail.
- The gate covers 1,350 physical creation bindings, H0-to-H1 first consumption,
  H0 retirement in the H2 worker, and H0-H4 triple-buffer slot reuse.
- Canonical dry-run resolves to `continuous=true`, `loop_count=4`, 25 entries
  per horizon, and no epoch caps. Standard preflight is clean with zero owned
  containers, networks, interfaces, pins, or locks.

## Rerun Gate

- Previous failed run/stage: r116, H1 maintenance frozen compile.
- Cheap check now catching it: `actual_frozen_generation_compile` uses the real
  r116 topology and work manifest and requires 25/25 strict compiles.
- Adjacent lifecycle states covered: H0 creation producer, initial compiler,
  H1 additive producer/first consumer, H2 cleanup of H0, and H3/H4 slot reuse.
- Command source: canonical `rich_s4_operational_clean_bpf`; next run is r117.
- Forbidden work: live binding discovery, full attach/reload/scan, Linux native
  MPLS forwarding, hidden repair, fallback, future qdisc apply, or early flip.
- Remaining risk: real kernel operation timing and H-boundary worker execution
  still require the 4H run; frozen data-shape mismatches no longer do.

## Watch Next

- Confirm H1 publishes ready before the H0 boundary.
- Confirm H2 cleans retired H0 only after the H1 flip, then H3 cleans H1.
- Confirm all 100 runtime epochs remain map-only at boundaries with zero
  fallback and no synchronous observer/report cost.

## r117 Follow-up

- r117 passed all offline preparation, compose, and container readiness, then
  failed during the first H0 veth creation before runtime. `ip link set`
  succeeded, but reading `/sys/class/net/<if>/ifindex` after `nsenter -n` used a
  sysfs mount that did not expose the entered network namespace.
- Binding capture now uses `ip -j link show dev` in the same netns command and
  parses the structured result. The pre-long-run gate creates and removes one
  real veth through the production method, so this exact kernel boundary is
  checked before another compose.

## r118 Follow-up

- r118 completed H0 physical prewarm in 326.20s and current-horizon install in
  145.00s, then strict initial BPF compilation failed in 3ms on
  `netns:gw201:ix200` before any runtime epoch. This interface is created by
  compose/Seed, not by the Skyseed veth preparer, so the veth result cache could
  never own it.
- Initial bootstrap now runs the compiler offline to derive the exact binding
  requirement set, compares it with H0 creation results, and captures only the
  explicit difference with one targeted `ip -j link show dev` per ref. It uses
  only the prewarm PID cache, forbids PID lookup on miss, performs no namespace
  or interface scan, and remains outside worker/runtime paths.
- The expanded companion gate replays r118 H0 and identifies exactly 12
  compose-created GW IX refs, then completes a strict H0 compile. Together with
  r116 replay it now covers strict H0 first consumption and H1 25/25 compile.
