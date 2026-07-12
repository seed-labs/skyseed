# r121: parent prepared-state handoff

Date: 2026-07-10
Status: stopped

## Goal

- Complete one canonical 4H `operational_clean` / `bpf_owned` run with 100/100
  runtime epochs, asynchronous H1-H3 maintenance, zero forbidden work, and a
  clean post-run preflight.
- Hand the process worker's additive physical ownership to the parent runtime
  as frozen metadata before the first consumer at each H boundary.

## Findings

- r120 ran 25/25 H0 epochs and consumed a ready H1 artifact before failing on
  `H1:starlink-shell-slice-0001` host apply.
- The real r120 worker input records 681 parent-owned resources and a 91-resource
  H1 delta. The old ready artifact published only the 676-resource target
  window; the correct additive ownership union is 772 resources over 578 slots.
- The real H1 first host plan contains 28 operations. With the old parent index,
  `gw205-gateway-slot0 -> stl-940316` and
  `gw209-gateway-slot0 -> stl-940277` fail the installer's prepared-resource
  guard. Both are explicit H1 delta resources.
- r121 passed compose, H0 physical preparation, the H0 current install, and all
  25 generation preloads. It completed 10 H0 runtime epochs, then failed closed
  on `H0:starlink-shell-slice-0011` with
  `bpf_owned_epoch_validation_miss_drop`.
- The miss samples were not selected data-plane flows or missing generation
  entries. Host Avahi emitted mDNS from gateway-addressed experiment bridges:
  `10.132.128.1 -> 224.0.0.251` reached `netns:gw203:ix202`, and
  `10.133.128.1 -> 224.0.0.251` reached `netns:gw211:ix210`. Both correctly hit
  the strict classifier-miss drop path.
- H1 maintenance completed its 91-resource additive prepare in 193.94 seconds
  while failure cleanup was already deleting the 681 parent-owned resources.
  The runtime cleanup therefore reported 91 unconsumed worker interfaces and
  `all_cleanup_success=false`. Compose teardown subsequently removed them and
  the post-run preflight was clean, but runtime rollback was correctly rejected.

## Changes

- Additive prepare now reports the installer's complete prepared-owned resource
  index in addition to the bounded delta it processed.
- The horizon worker validates that its post-prepare owned index exactly equals
  `existing ownership + delta`, then publishes that union in the ready artifact.
  The future window's required scope remains a separate field.
- The parent accepts the frozen ready-artifact ownership as metadata only. It
  rejects dropped old resources and PID-cache drift, performs no physical
  prepare or scan, preserves its active topology/link state, and does not flip
  a generation or expose future links/qdisc state.
- Host apply failures now retain the installer's underlying failure reason for
  subsequent artifact reconstruction.
- The companion gate adds a real r120 replay using its worker input, ready
  artifact, event sequence, and a targeted extraction of the frozen H1 first
  host plan.

## Validation

- Focused worker/runtime/gate regression: 261/261.
- Full requested 14-module regression: 574/574.
- Companion gate schema v4: 13/13 checks pass. The r120 replay positively
  reproduces the old dropped-resource rejection, finds both missing first
  consumer resources, then validates all 772 owned resources and all 28 first
  consumer resources after the metadata-only handoff.
- The 25-generation worker replay is also forced through a no-delta lifecycle:
  it restores the frozen PID/owned state once, performs zero physical prepares,
  completes final binding freeze, and publishes ready.
- r121 canonical dry-run renders the only active profile with `--continuous`,
  `--runtime-mode operational_clean`, `--loop-count 4`, and
  `--forwarding-backend bpf_owned`; it has no epoch cap or alternate entry.
- Clean preflight is ready with zero containers/networks/interfaces/locks and
  zero BPF maps/programs. Workspace free space is about 31.9 GiB.
- `git diff --check` passes.

## Rerun Gate

- Previous failed run/stage: r120, immediately after H1 ready consumption and
  before the first H1 host operation could be applied.
- Cheap check now catching it: `parent_host_prepare_handoff_replay` runs the
  real installer guard against the old and corrected scopes and checks the real
  H1 first consumer plan.
- Adjacent lifecycle states covered: H0-only parent ownership; H1 delta and
  no-delta worker publication; first boundary consumer; H2/H3 retired generation
  cleanup; H4/H5 three-slot reuse without a special case.
- Command source and run id: the sole
  `rich_s4_operational_clean_bpf` profile as
  `r121-4h-parent-prepared-state-handoff`.
- Forbidden work: Linux MPLS/LFIB, fallback, full reload, full scan, live
  dump/diff, hidden reconcile, future link/qdisc exposure, early active flip,
  synchronous worker work, and runtime-critical audit/observer work.
- Remaining late-failure risk: real multi-boundary kernel/container execution
  and later H2/H3 ownership handoffs; these must be observed through full
  completion rather than inferred from H1.

## Companion Command

```bash
PYTHONPATH=satellite:satellite/apps python \
  satellite/apps/pre_long_run_contract_check.py \
  --candidate-profile-run-dir /tmp/skyseed-r121-gate/r121-canonical-dry-run \
  --worker-failed-run-dir satellite/output/validation_profiles/r116-4h-frozen-direct-maintenance \
  --cleanup-failed-run-dir satellite/output/validation_profiles/r113-final-continuous-4h \
  --initial-binding-failed-run-dir satellite/output/validation_profiles/r118-4h-ip-json-binding-handoff \
  --final-binding-conflict-run-dir satellite/output/validation_profiles/r119-4h-complete-bootstrap-binding-producers \
  --parent-host-handoff-failed-run-dir satellite/output/validation_profiles/r120-4h-final-binding-freeze \
  --multicast-cleanup-failed-run-dir satellite/output/validation_profiles/r121-4h-parent-prepared-state-handoff \
  --compose-multicast-gap-run-dir satellite/output/validation_profiles/r122-4h-unicast-worker-quiesce \
  --expected-horizon-count 4 \
  --expected-entries-per-horizon 25
```

## Watch Next

- H1 ready must publish 772 owned resources and the parent handoff must report
  91 additions while preserving active state.
- H2 and H3 workers must publish monotonically additive ownership, clean only
  the retired generation horizon, and never block an epoch.
- Require 100/100 epochs, aggregate success, zero fallback/forbidden counters,
  real nonblocking ping observations, and clean teardown.
- Superseded by `2026-07-10_r122-unicast-worker-quiesce.md`.
