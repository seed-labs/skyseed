# r123: frozen compose IX unicast preparation

Date: 2026-07-10
Status: stopped; H0 complete, H0->H1 parent PID-cache handoff failed closed

## Goal

- Complete the canonical 4H `operational_clean` / `bpf_owned` profile after
  closing the compose-created IX bridge/endpoint multicast producer boundary.

## Findings

- r122's original real compose file had no deterministic
  `com.docker.network.bridge.name` for `net_ix_ix202` or `net_ix_ix210`.
  Docker created random bridge names, so the physical prepare path could not
  consume them as frozen explicit inputs without discovery.
- The H0 targeted binding producer already captures every required interface
  from frozen refs (234 in the r118 replay), including all compose-created GW
  IX endpoints. It is the correct initial physical boundary for disabling
  endpoint multicast while returning the frozen ifindex/MAC result.

## Changes

- Preview compilation rewrites only the 12 explicit `net_ix_*` compose networks
  with deterministic 15-character bridge names and records the exact mapping in
  `frozen_compose_ix_bridge_bindings.json`. Internal AS/dynamic networks are not
  changed, preserving any routing-protocol multicast outside strict GW IX
  classifier scope.
- Before H0 prewarm/BPF attach, runtime consumes that frozen mapping and turns
  multicast off on each exact IX bridge. It performs no interface scan or
  Docker discovery and fails immediately on a missing/mismatched bridge.
- Targeted existing-interface binding capture now performs
  `multicast off + ip -j link show` in the same frozen PID/netns operation. The
  result is rejected unless every requested endpoint is unicast-only.
- r122's stopped-run artifact records the live split: 763 direct veths with zero
  multicast flags versus the two compose IX bridge/endpoint pairs still
  multicast-capable. The companion replay rewrites the real r122 compose file
  and checks exact deterministic bindings.

## Validation

- Full repository unit discovery: 746/746.
- Real Docker IX gate creates a network with the frozen bridge driver option and
  a compose-like endpoint. Both start with `MULTICAST`; the production bridge
  prepare and targeted endpoint capture remove it from both, then cleanup leaves
  no bridge/veth/network residue.
- r118 initial producer replay strict-compiles with 234 targeted refs and now
  requires the unicast physical-prepare contract.
- Companion schema v6 passes 17/17 and covers the r121 miss/cleanup failure and
  r122 compose producer gap together.
- Canonical r123 dry-run resolves the single continuous 4H
  `operational_clean` / `bpf_owned` profile with no epoch cap. Clean preflight
  has zero containers, networks, direct interfaces, locks, and BPF pins, with
  about 28.3 GB free.
- `git diff --check` passes.

## Run Outcome

- The canonical run completed H0 at 25/25 epochs and stopped before the first
  H1 epoch with `horizon_maintenance_handoff_pid_cache_mismatch`. The H1
  maintenance worker had already published a ready artifact with 25 inactive
  generations and no blockers; the parent rejected the prepared-state handoff
  because its initial PID cache was populated but had never been marked
  complete.
- H0 crossed the r121 epoch-11 failure point with
  `bpf_classifier_miss_count=0`; Linux fallback and eligible-core fallback
  counts were both zero. The frozen 12-IX bridge and endpoint unicast prepare
  therefore closed the observed mDNS ingress failure without changing SEED
  topology ownership.
- Bootstrap generation preparation is not clean-path compliant. The 24
  inactive H0 generations took `1047.088191s`: repeated logical interface
  discovery/binding used `693.411s`, repeated live clsact reuse guards used
  `311.331s`, while all inactive BPF batch map writes used only `3.598s`.
  Bootstrap still omits frozen compiled inputs and prepared attach-capability
  proof even though the maintenance worker consumes both.
- Final bounded cleanup included the frozen 91-resource H1 worker delta,
  completed without a global existence scan, and left zero Compose containers,
  networks, direct interfaces, runtime locks, or BPF pins.
- Aggregate result is not a successful 4H validation: 25/100 runtime
  transactions completed and `aggregate_runtime_succeeded=false`.

## Rerun Gate

- Failed run/stage: r123 after H0 25/25, while consuming H1 worker readiness at
  the H0->H1 boundary.
- The r123 companion gate did not catch either blocker. Its H0 generation replay
  checked compile results but not frozen-input provenance, zero discovery/guard
  work, or cost. Its parent handoff fixture used restored state, which marked
  the PID cache complete and skipped the real initial-parent state transition.
- Required adjacent lifecycle replay now includes the actual initial parent
  producer, H0 frozen preload, H1 process-worker restore, parent boundary
  accept, H2/H3 retired cleanup, and H5+ triple-slot reuse.
- No next run is authorized from this round. Linux MPLS/LFIB, fallback, full
  reload/scan, live diff, discovery, hidden reconcile, early visibility/flip,
  synchronous epoch maintenance, and runtime-critical observer/audit work
  remain forbidden.

## Companion Command

```bash
PYTHONPATH=satellite:satellite/apps python \
  satellite/apps/pre_long_run_contract_check.py \
  --candidate-profile-run-dir /tmp/skyseed-r123-gate/r123-canonical-dry-run \
  --worker-failed-run-dir satellite/output/validation_profiles/r116-4h-frozen-direct-maintenance \
  --cleanup-failed-run-dir satellite/output/validation_profiles/r113-final-continuous-4h \
  --initial-binding-failed-run-dir satellite/output/validation_profiles/r118-4h-ip-json-binding-handoff \
  --final-binding-conflict-run-dir satellite/output/validation_profiles/r119-4h-complete-bootstrap-binding-producers \
  --parent-host-handoff-failed-run-dir satellite/output/validation_profiles/r120-4h-final-binding-freeze \
  --multicast-cleanup-failed-run-dir satellite/output/validation_profiles/r121-4h-parent-prepared-state-handoff \
  --compose-multicast-gap-run-dir satellite/output/validation_profiles/r122-4h-unicast-worker-quiesce \
  --latest-failed-run-dir satellite/output/validation_profiles/r123-4h-compose-ix-frozen-unicast \
  --expected-horizon-count 4 \
  --expected-entries-per-horizon 25
```

## Watch Next

- Before H0 prewarm, exactly 12 frozen compose IX bridges must report
  multicast disabled; final binding capture must report all requested refs
  unicast-only.
- Require 100/100 epochs, zero BPF miss/drop and fallback/forbidden counters,
  process-worker ready before each boundary, natural three-slot cleanup, bounded
  successful rollback, and clean post-run preflight.

## Repair Requirements Recorded At Stop Time

- Delete the optional bootstrap live compile/discovery and live clsact-guard
  route. Pre-runtime inactive preload must require frozen compiled inputs and a
  single prepared attach-capability proof, and fail immediately when either is
  missing.
- Extend the full-lifecycle companion gate so H0 bootstrap, not only the H1
  process worker, requires frozen input provenance, zero discovery/guard work,
  and bounded preload cost.
- Make the initial parent prepared-state producer explicitly seal its populated
  frozen PID cache as complete before a worker request is emitted. Replay the
  real bootstrap-parent -> worker-ready -> boundary-accept sequence; a fixture
  restored directly into worker state is insufficient.
- Do not start another expensive run until both blockers pass the broad
  H0/H1/H2/H3 and H5+ lifecycle replay.

## Postmortem Repair Completed (No New Long Run)

- The initial final-binding producer now seals the exact populated container
  PID cache. The maintenance request can only consume a sealed snapshot.
  Replaying the r123 handoff now produces
  `horizon_maintenance_handoff_pid_cache_not_sealed` before seal and succeeds
  with the same 388 PID values after seal; actual PID drift remains a separate
  mismatch with missing/unexpected/drift detail.
- H0 target generation compilation has one path: strict frozen compile followed
  by preload using prepared attach authority. The old per-generation live
  discovery and live clsact-guard route, its runtime lookahead parameter, and
  its dead freeze/union implementations were deleted.
- The worker no longer offline-compiles 25 generations to discover interface
  refs before strict-compiling them again. It extracts a finite endpoint-ref
  union directly from each frozen context, captures that exact set once after
  additive preparation, and shares the final cache across all 25 strict
  compiles.
- Exact binding capture still runs `ip -j link show dev` for each requested
  interface, but interfaces are grouped into one `nsenter` command per frozen
  node and node batches run with at most 16 workers. Maintenance delta attach
  likewise runs at most 16 distinct-interface tasks after physical creation.
  It remains delta-only and has no full attach/reload alternative.
- r123 measured `77.435956s` physical preparation, `56.863001s` sequential
  attach, and about `70.174644s` remaining capture/compile/report work. Using a
  conservative `10s` 25-generation strict-compile budget and the new bounded
  batching gives a `95.375809s` core worker projection. This is a gate budget,
  not a claimed live measurement.
- Ready/handoff generation records now retain activation identity and compact
  proof only. The synthetic nested-map replay is 1250 bytes. Operational-clean
  epoch summaries no longer copy nested readiness/compiled results; copied
  immutable artifacts and profile stdout use hardlinks without a copy fallback.
- Final cleanup deletes only owned host veths and bridges. The 1350 paired
  container endpoints and 1310 completed temporary names observed in r123 are
  elided because host-veth deletion/rename already owns those lifecycles.

## Postmortem Validation

- Clean operational preflight: zero scoped containers, networks, direct host
  interfaces, runtime locks, and BPF pins; about 26.3 GB free.
- Requested broad suite: 584/584.
- Companion schema v7: 18/18, including r113 cleanup and r116-r123 real failure
  materials, r119 25/25 strict compile with live lookup patched to fail,
  H0-H5 three-slot cleanup semantics, r123 PID seal replay, old-cost evidence,
  bounded-parallel source contracts, and artifact compaction.
- `git diff --check` passes.
- No canonical 4H run was started after these fixes, per the explicit pause
  request.
