# Experiment Round Ledger

This directory records Skyseed experiment work by round. Keep one short file per
round so future sessions can reconstruct what changed, why it changed, what was
validated, and what remains.

- `2026-07-11_r134-shared-gz-architecture-offline-gate.md`: immutable UT LPM
  policy, gateway selectors, shared G/Z FECs, explicit GW service loopbacks,
  zone-edge delivery, and the completed 100-generation offline gate.

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

## Rerun Gate

- Previous failed run/stage:
- Cheap check or targeted replay that now catches it:
- Adjacent lifecycle states covered:
- Command source and run id:
- Forbidden-work expectations:
- Remaining late-failure risks:

## Remaining

- ...

## Watch Next

- ...
```

Keep entries concise. Put exact commands, run ids, and artifact paths only when
they are needed to reproduce or compare the round. Prefer durable categories
over one-off details.

## Recent Entries

- `2026-07-12_r141-compact-worker-state-alias.md`: compact worker ownership
  reached the real H1 boundary, where the BPF consumer rejected equivalent
  frozen/live source-state namespaces. Exact snapshot/epoch alias matching is
  now shared by the consumer contract, with stale identities still fail-closed;
  actual failed-artifact replay and 791 tests pass before rerun.

- `2026-07-11_r138-r140-consumer-pop-ix-and-4h-success.md`: ordered consumer
  replay catches the r138 first-transaction mismatch before compose; r139 then
  exposed a retired IX maintenance binding and a removed kernel-5.4 BPF manual
  pop fallback. Both were repaired and gated. r140 completed 100/100 across
  H0-H3, all maintenance workers ready, real ping coverage across both policy
  classes, and clean teardown.

- `2026-07-11_r133-4h-dataplane-success-observer-gap.md`: r133 achieved the
  first complete 4H dataplane lifecycle, 100/100 with clean teardown and zero
  fallback, including H3 generation zero. It is not final experiment evidence
  because the canonical profile had dropped the nonblocking ping-observer
  interval and produced zero observations. The profile/session/companion
  command contract now requires the proven 30-second raw observer before r134.
- `2026-07-11_r132-generation-zero-h3-consumer.md`: r132 completed H0-H2
  75/75 and passed the former r131 drift point, then rejected the valid H3
  physical generation `0` because the consumer converted falsey zero to `-1`.
  Explicit zero-value lookup/removal and active-cleanup semantics now replay the
  real H3 producer, first consumer, immediate successor, and H0-H5 lifecycle;
  772 tests and the 24/24 companion gate pass before r133.
- `2026-07-10_r123-compose-ix-frozen-unicast.md`: r123 completed H0 25/25 with
  zero classifier miss and fallback, then failed closed at H0->H1 because the
  populated parent PID cache was never marked complete. The run also exposed
  the remaining bootstrap-only old route: 24 inactive generations repeated
  live discovery and clsact guards for 1047s even though map writes took 3.6s.
  The postmortem repair deletes that old route, seals the initial PID cache,
  removes duplicate worker requirement compiles, batches exact binding capture
  and delta attach with bounded parallelism, compacts generation artifacts, and
  removes redundant cleanup deletes. The 584-test suite and 18/18 companion
  replay pass; no next long run was started.
- `2026-07-10_r122-unicast-worker-quiesce.md`: r121's H0 epoch-11 failure is
  traced to host Avahi mDNS entering multicast-capable gateway bridges, while
  concurrent H1 preparation left exactly 91 future interfaces outside parent
  cleanup. Physical links are now unicast-only; teardown quiesces the worker
  process group and deletes the frozen 681 + 91 owned union without scanning.
- `2026-07-10_r121-parent-prepared-state-handoff.md`: r120's H1 parent/runtime
  ownership gap is replayed with its real 681 + 91 -> 772 resource union and
  first host consumer. r121 is gated on a metadata-only, fail-closed handoff.
- `2026-07-10_r120-final-binding-freeze-gate.md`: r119's conflicting readiness
  and active-generation MAC sources are replaced by one ordered final binding
  freeze. The companion gate now replays r116-r119 together, captures exactly
  409 r119 H1 refs, strict-compiles 25/25 generations without live lookup, and
  verifies H0-H5 cleanup/three-slot semantics before the canonical r120 run.
- `2026-07-10_r116-frozen-endpoint-binding-handoff.md`: r116 completed six fast
  H0 epochs with zero fallback, then failed closed because worker compilation
  did not receive complete bindings for non-attached H0-prepared and newly
  created H1 interfaces. H0 and additive veth creation now return MAC/ifindex
  operation results, initial and worker compilers consume one strict frozen
  cache, and the real r116 H1 manifest compiles 25/25 offline with live lookup
  disabled before the r117 rerun. r117 then exposed that netns sysfs was not a
  valid ifindex result channel during H0 creation; capture now parses
  `ip -j link show dev`, and the standard gate exercises one real production
  veth create/capture/cleanup before compose. r118 then identified the distinct
  compose-created GW IX producer boundary; a targeted 12-ref bootstrap capture
  now completes strict H0 compilation without entering worker/runtime paths.
- `2026-07-09_r115-frozen-direct-maintenance.md`: r115 completed all 25 H0
  runtime epochs with zero fallback but missed H1 because 25 process-owned
  generation compiles repeated live ifindex/MAC discovery and the inherited
  attach path probed reused clsact state. Maintenance now shares one complete
  frozen binding cache, executes only necessary direct delta attach operations,
  preserves attach capability across H flips, and has a companion check that
  fails on live discovery or validation-only attach.
- `2026-07-09_r114-process-worker-frozen-pid-cache.md`: r114 completed all 25
  H0 runtime epochs with zero fallback, then failed closed because the new
  exec-based H1 maintenance worker discarded the parent's container PID/netns
  caches and missed the 240-second boundary. Process work v2 now carries one
  frozen PID map for host prepare and BPF canary use, forbids cache-miss Docker
  lookup, and extends the companion gate with both r114 worker and r113 cleanup
  regression evidence.
- `2026-07-09_r113-final-continuous-4h-gate.md`: r113 passed the r112 4H
  continuous queue gates, completed compose and 6 H0 runtime transactions, then
  failed closed before H1 because the maintenance worker's context compile path
  treated a missing path as `.`. The fix makes process payload physical paths
  the worker's only path source, adds r113-shaped offline replay coverage, and
  makes failed real-runtime reports return nonzero.
- `2026-07-09_r112-runtime-queue-coverage-4h.md`: r112 failed before the
  first runtime epoch. It exposed two offline-detectable contract gaps: live
  runtime pre-prepare reached back to an out-of-scope `normal_runtime_inputs`
  variable, and the active 4H profile had `loop_count=4` without emitting
  `--continuous`, so only the initial 25-entry segment was frozen. The fix
  makes live route-baseline preparation consume `segment_queue + context_by_key`,
  requires continuous mode for multi-horizon long-run profiles, emits
  `--continuous` from the profile command builder, and extends runtime queue
  coverage with a horizon-scope check that catches r112 locally as
  `PREPARED_HORIZON_SCOPE_COUNT_MISMATCH`.
- `2026-07-09_r111-route-evidence-cache.md`: the intended 4H clean profile
  launched and prepared a 96-generation window, then failed closed at epoch 4
  because GW local-origin return route evidence for UT2021/UT2022 was prepared
  but not carried beyond previous active-session contexts. The fix adds compact
  immutable prepared route-identity evidence, carries it in the clean BPF
  session backend, expands pre-runtime route baseline coverage to the runtime
  queue route contexts, and keeps runtime route apply/fallback/live probes at
  zero. It also adds the independent runtime queue coverage audit and
  precompose blocker artifact so missing frozen contexts, invalid BPF
  generation slot assignments, and frozen route identity mismatches are caught
  before compose as part of the standard clean pre-run flow, not an H4-only
  smoke check. Focused tests, 436 regression tests, compile checks, offline
  r109/r110/r111 artifact replay, and post-run session cleanliness pass.
- `2026-07-09_r108-visualization-physical-link-contract.md`: active clean S4
  profile now uses an explicit 12-GW binding from Skyseed IDs to visualization
  gateway IDs, real catalog coordinates, visualization frozen GW/ISL links,
  and Skyseed-owned UT access geometry. The current serviceable active set is
  28 UTs across 14 zones; two unserviceable current-window zones are excluded
  as a temporary profile selection, not a durable zone rule. Focused tests,
  267 profile/session/topology tests, profile dry-run, and real-coordinate
  preview-only serviceability gate pass with unresolved active zone count `0`.
- `2026-07-09_r107-visualization-source-and-cap-removal.md`: r107 ended with
  aggregate success but was not valid 4H evidence because the underlying command
  still had `--max-parameter-epochs 1` and only produced 22 `initial` epoch
  rows. The fix removes long-run caps from the active profile, rejects cap
  reintroduction, routes orbit and gateway inputs through
  `apps/visualization/`, removes the old session canonical profile, and updates
  active docs/session checks so the next command source is the single 4H
  `rich_s4_operational_clean_bpf` validation profile with no old 1-shell
  goal-readiness handoff. No new long run was started.
- `2026-07-09_r106-r107-single-path-cleanup.md`: r106 was a mislaunched
  capped short run that completed only four runtime transactions and is not H4
  evidence. Active retired launch/profile/probe/controller surfaces were
  deleted rather than guarded: only `rich_s4_operational_clean_bpf` remains in
  the profile list, the profile contract rejects runtime epoch caps, the
  controller-backed runtime branch and legacy controller sources are gone, and
  709 unit tests plus full-profile dry-run pass.
- `2026-07-09_r102-r103-dynamic-worker-budget-frozen-request.md`: r102 reached
  H0 runtime in the 4H clean run and failed closed before H1 because the
  process worker attach prepare consumed the default `256` attach budget even
  though the work input carried a dynamic budget of `708`. The fix carries the
  dynamic resident attach budget through frozen manifest proofs, fails closed if
  a process worker reports default budget consumption, and changes process
  preload work items to require frozen compiled inputs instead of context-based
  worker recompilation. r103 4H no-compose passed with blockers `0` and dynamic
  rolling budget `633`; live 4H is still required to validate H-boundary worker
  execution.
- `2026-07-09_r101-preflight-slim-freeze-gate.md`: r101 validated the 4H
  no-compose prepare gate after prepared artifact slimming. Normal runtime
  inputs now keep scoped canonical contexts and host mutation plans, while
  per-segment context/runtime-plan/batch/snapshot indexes and top-level H0
  context copies are derived in memory instead of persisted. The 4H gate passed
  with H count 4, selected/current queue count 25, blockers 0, compact summary
  about 3.6KB, executable inputs about 0.55MB, and normal inputs about 319MB.
  The remaining prepare-only hotspot is `prepare_frozen_horizon_segments`, about
  92s in r101. Session/preflight gates are clean after removing obsolete failed
  r97/r98 output directories; r93 and r101 evidence were kept.
- `2026-07-09_r104-r105-worker-deferred-freeze.md`: r104 reached runtime in the
  4H operational-clean run, completed the first H0 runtime epoch, then failed
  during H1 replenishment because the main runtime loop tried to live-freeze
  future generation BPF inputs before the process worker had prepared the
  future physical interfaces. The fix moves future generation freeze/compile
  into the process worker after additive host prepare, while retaining dynamic
  resident attach budget guards and fail-closed behavior when context compile is
  not explicitly allowed. Focused worker/runner tests and r105 4H
  prepare-only-no-compose gate pass; r105 does not prove the live H1 worker
  boundary because no compose/runtime worker artifacts are created.
- `2026-07-09_r98-worker-budget-node-local-regression.md`: r98 reached H0
  runtime in the 4H operational-clean run and completed 16 runtime epochs, then
  failed closed before H1 because the process worker compact manifest did not
  carry the rolling resident attach budget. The worker fell back to the default
  `256` attach budget and rejected a valid `288` interface H1 scope. The same
  run exposed a runtime regression: BPF-owned edge route preflight state was
  not persisted, so UT default/GW return route replacements were regenerated
  every epoch and node-local apply rose to about `41s` average. Fixes preserve
  the rolling budget through worker frozen inputs, add an offline audit blocker
  for missing worker budgets, persist BPF edge route session state, classify
  clean BPF node-local work as edge-only endpoint routes, and externalize normal
  runtime inputs so executable runtime inputs carry only a hash/path reference.
- `2026-07-09_r97-4h-startup-hard-cap-self-kill.md`: r97 attempted the first
  4H operational-clean run for loop-general validation, but did not reach
  runtime. Compose startup was still progressing at `81/84` batches with Docker
  responsive and recent container growth, yet the startup contract killed the
  run on the fixed `1500s` hard cap. The fix removes fixed whole-startup elapsed
  caps/warnings from operational clean, keeps only stage/progress/failure
  supervision for startup, cleans legacy `/home/seed/seed-emulator` from worker
  subprocess `PYTHONPATH`, externalizes executable runtime inputs out of the
  prepared bundle, and records the remaining artifact-sharding optimization.
- `2026-07-08_r95-worker-ping-route-fail-closed.md`: r95 intentionally
  exercised the new process maintenance and ping observer paths, completed H0
  runtime cleanly for 25 epochs, then failed closed at the H1 boundary because
  the real in-process host manager lacked `restore_prepared_runtime_state`.
  It also split ping failures into UT no-route versus post-route packet-loss
  buckets and led to focused fixes for manager restore, compact worker input,
  active UT edge-route baseline coverage, and the clean GW-local-origin BPF
  return handoff for selected-GW ping replies.
- `2026-07-08_r93-clean-runtime-qdisc-success.md`: r93 completed the full
  3H/240s `operational_clean` / `bpf_owned` validation after qdisc batching and
  clean-runtime cleanup, with 75/75 runtime epochs, H1/H2 ready artifacts
  consumed at runtime boundaries, fallback counters at 0, and qdisc process
  churn reduced to 189 batch invocations for 17,908 sub-updates. UT-to-selected
  GW ping observation is nonblocking but still failing and is the next semantic
  item.
- `2026-07-08_r91-r92-preloaded-generation-gate-gap.md`: r91 exposed
  frozen/live BPF intent drift at H2 slice0003, and r92 exposed that the cheap
  gate missed immediate successor consumption after a successful preloaded
  generation transition. The blocker was resolved by lifecycle tests and r93,
  but the entry remains the durable reminder that narrow handoff fixes need
  first-consumer and immediate-successor coverage before another expensive run.
- `2026-07-08_r90-qdisc-batch-executor.md`: direct netns qdisc batch executor
  implemented for parameter update groups and structural activate/deactivate
  lifecycle stages; r93 validated the executor in a full 3H operational run.
- `2026-07-06_r89-attach-capability-freeze-inputs.md`: attach-capability
  maintenance now consumes frozen compiled input manifests before preparing the
  capability contract, avoiding broad live host-interface lookup in that stage;
  focused runtime validation and BPF live tests pass, with operational
  validation still pending.
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
