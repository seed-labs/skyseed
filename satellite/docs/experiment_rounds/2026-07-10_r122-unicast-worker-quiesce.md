# r122: unicast physical links and worker quiesce

Date: 2026-07-10
Status: stopped

## Goal

- Complete the canonical 4H `operational_clean` / `bpf_owned` profile with all
  100 runtime epochs, H1-H3 process maintenance, strict zero fallback, and a
  clean bounded rollback.
- Eliminate host multicast injection at physical preparation and make failure
  teardown race-free when a future-horizon worker is still mutating resources.

## Findings

- r121's exact miss/drop samples are host mDNS, not a generation-slot or map
  content defect. Both packets came from `.1` addresses assigned to prepared
  bridges and entered gateway IX classifier interfaces as `224.0.0.251`.
- r121's H1 worker owned exactly 91 resources absent from the parent's 681
  resource index. Those same 91 host interfaces remained after parent cleanup;
  the correct bounded cleanup union is 772 resources.
- The prior teardown deleted parent artifacts before terminating the maintenance
  process. A worker could therefore create resources after its scope had already
  been cleaned.
- r122 passed all offline gates, started 458/458 containers with zero retry,
  and reached H0 physical prewarm. A live pre-runtime inspection found all 763
  newly prepared direct veths unicast-only, but the actual r121 ingress path was
  different: compose-created `gw203:ix202` / `gw211:ix210` and their Docker IX
  bridges still had `MULTICAST`. r122 was stopped before BPF attach/runtime
  rather than spend the remaining run on a known repeat failure. No manual
  runtime mutation was performed; standard teardown and post-run preflight were
  clean.

## Changes

- Prepared bridges and both veth endpoints are explicitly `multicast off`
  before activation. SkySeed's physical slot data plane is unicast-only; ARP
  broadcast remains available, while Avahi/LLMNR/IPv6 multicast cannot enter a
  strict classifier port.
- Maintenance workers start in their own process sessions. Runtime teardown
  first terminates the entire worker process group and waits for it to become
  terminal, then performs host cleanup.
- Every queued worker records its already-frozen delta owned scope. Failure
  cleanup unions that scope with the parent's owned index and deletes only that
  bounded inventory. It does not scan globally, accept future state into the
  active runtime, reconcile, or perform fallback cleanup.
- The companion gate now extracts the two r121 miss samples and `final_cleanup`
  from the large real artifacts without loading their 406 MiB/976 MiB records.
  It proves the 681 + 91 = 772 union, the exact 91-interface omission, worker
  quiesce ordering, and bounded cleanup with no global fallback.

## Validation

- Focused changed-path tests: 120/120.
- Full repository unit discovery: 744/744.
- Companion gate schema v5: 15/15. The r121 replay finds two mDNS samples,
  91 worker-delta resources, 91 future-only host interfaces, and replays a
  successful bounded union cleanup after worker quiesce.
- The real physical gate creates a gateway-addressed bridge and veth pair in a
  real network namespace, observes `MULTICAST` absent on bridge, host veth, and
  container veth, then removes all three without residue.
- Canonical r122 dry-run resolves the single
  `rich_s4_operational_clean_bpf` profile with `--continuous`,
  `operational_clean`, loop count 4, `bpf_owned`, and no epoch caps.
- Clean preflight reports zero containers, networks, direct host interfaces,
  locks, and pinned BPF objects. About 29.5 GB remains free.
- `git diff --check` passes.

## Rerun Gate

- Previous failed run/stage: r121, H0 epoch 11, while H1 maintenance was still
  preparing future resources.
- Cheap checks now catching it: `real_unicast_physical_link` and
  `r121_multicast_and_worker_cleanup_replay`.
- Adjacent lifecycle states covered: all 25 H0 generations; H1 additive and
  no-delta worker paths; H1 first consumer; H2/H3 retired cleanup; H4/H5
  three-slot reuse; normal completion and failure teardown while a worker is
  running or already terminal.
- Command source and run id: the sole
  `rich_s4_operational_clean_bpf` profile as
  `r122-4h-unicast-worker-quiesce`.
- Forbidden work: Linux MPLS/LFIB, fallback, full reload, full scan, live diff,
  hidden reconcile, future-state visibility, early flip, synchronous epoch
  maintenance, and runtime-critical observer/audit work.
- Remaining late-failure risk: real H1-H3 boundary execution and generation
  reuse still require the complete 4H run; no local result substitutes for it.

## Companion Command

```bash
PYTHONPATH=satellite:satellite/apps python \
  satellite/apps/pre_long_run_contract_check.py \
  --candidate-profile-run-dir /tmp/skyseed-r122-gate/r122-canonical-dry-run \
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

- No classifier miss/drop may be attributable to host bridge control traffic.
- At H1, ready ownership must be 772 and parent handoff additions must be 91.
- H2/H3 must clean only retired generations and preserve the three-slot cycle.
- Require 100/100 epochs, zero fallback/forbidden counters, asynchronous
  maintenance, nonblocking ping observation, bounded rollback, and clean
  post-run preflight.
- Superseded by `2026-07-10_r123-compose-ix-frozen-unicast.md`.
