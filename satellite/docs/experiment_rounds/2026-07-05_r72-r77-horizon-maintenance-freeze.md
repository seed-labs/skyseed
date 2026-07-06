# r72-r77: Horizon Maintenance Contract Freeze

Date: 2026-07-05
Status: fixed; frozen for next-round validation

## Goal

- Continue from r71 by making next-horizon maintenance an explicit contract
  instead of hidden synchronous work inside the epoch path.
- Fix queued H1 preload readiness so it consumes real base/current generation
  manifests, never compact summaries.
- Remove repeated runtime discovery, binding, and live attach guard work from
  inactive preactivation preload. The worker must consume frozen manifests and
  prepared attach-capability proof.
- Freeze this big round after code and focused tests; the next fresh 3H/240s
  run belongs to the next conversation.

## Findings

- r71 stopped before H1 queued preload with
  `rolling_horizon_bpf_preload_base_compiled_inputs_missing:H1`.
- r76 reached the queued worker path, but failed after partial preload because
  `host_interfaces` was not plumbed into the worker preactivation path.
- r77 proved the H1 worker could complete, but it published readiness about
  `11s` after the H0->H1 boundary. The main process correctly fail-closed
  before entering H1; H1 was not activated on partial or late readiness.
- r77 H1 worker timing showed the real bottlenecks:
  - inactive preload record `total_wall_seconds` sum: about `453.7s`
  - repeated interface discovery/logical binding: about `363.0s`
  - batch BPF map write: about `3.0s`
  - remaining overhead: about `87.6s`, mainly repeated live clsact guard/report
    work
- The BPF queued preload was already using batch map-write semantics. Its
  update count is sub-updates, not shell command count.
- H0 normal runtime epochs were not doing runtime attach, full reload, or heavy
  map writes. qdisc updates ran in `24/25` runtime epochs; the first reused
  pre-runtime readiness.

## Changes

- Added `satellite/horizon_maintenance.py` as the maintenance boundary:
  ready/blocked/failed classification, compact ready/failure artifacts, and
  fail-closed publication rules.
- Fixed the base compiled-input readiness contract:
  compact summaries are rejected, missing full inputs fail closed, and queued
  preload records expose manifest-only/batch audit fields.
- Added a prepared attach-capability proof contract. Worker preload now reuses
  this proof and does not repeat the live clsact attach guard in the inactive
  map-write loop.
- Added frozen compiled runtime inputs for queued horizon maintenance:
  - frozen per-generation inputs are produced before enqueueing work
  - full payload stays in the in-memory work item
  - artifacts expose compact summaries and validation checks only
  - the worker requires these inputs and skips live compile/discovery/binding
- Added fail-closed checks for missing, summary-only, schema-invalid,
  generation-mismatched, or map-conflicted frozen compiled inputs.
- Plumbed per-horizon ifindex/MAC caches through the BPF-owned compile and
  preactivation paths.
- Extended compact maintenance/preload records so reports can show frozen-input
  checks, compiled-input source, prepared attach proof status, reused-guard
  metrics, and timing fields.

## Validation

- Passed compile checks:
  `python -m py_compile satellite/linux_mpls_runtime.py satellite/apps/run_starlink_shell_experiment.py satellite/horizon_maintenance.py satellite/test_runtime_validation_defaults.py`
- Passed whitespace check for touched runtime/test modules:
  `git diff --check -- satellite/linux_mpls_runtime.py satellite/apps/run_starlink_shell_experiment.py satellite/horizon_maintenance.py satellite/test_runtime_validation_defaults.py`
- Passed focused tests covering:
  - frozen compiled inputs skip live compile/host lookup
  - missing frozen inputs fail closed
  - prepared attach-capability proof reuse
  - missing prepared attach proof fail closed
  - lookahead preload writes inactive maps without runtime apply
  - horizon maintenance artifact contracts
- No long 3H/240s run was executed after the final frozen-input and prepared
  attach-capability fixes.

## Remaining

- Validate the frozen-input worker path in a fresh 3H/240s operational run.
- Confirm H1 readiness is published before the H0->H1 boundary and then
  consumed by the main process.
- Confirm live worker preload reports:
  - `compiled_runtime_inputs_source=queued_horizon_maintenance_frozen_manifest`
  - near-zero `interface_discovery_logical_binding_wall_seconds`
  - near-zero reused attach guard time in the preload loop
  - `frozen_compiled_runtime_inputs_check.ready=True`
  - batch map-write audit fields
- H1->H2 maintenance and retired H0 cleanup dependency reporting remain
  unvalidated by a long run.

## Watch Next

- Use a fresh prepared input set for the next major iteration; old static
  `goal-1shell-*` evidence is reference-only.
- Keep the run fail-closed: no future host link activation, runtime full
  reload, full attach fallback, live full map dump/diff, generation GC scan, or
  target-generation cleanup scan.
- If the next failure is a field/contract alignment bug, fix and rerun within
  the allowed attempt budget. If it implies a larger worker architecture change,
  stop and report before another long run.
- After this contract is validated, move horizon maintenance/freeze/preload
  into the background worker/subprocess path so H0 epochs no longer wait for H1
  preparation.
