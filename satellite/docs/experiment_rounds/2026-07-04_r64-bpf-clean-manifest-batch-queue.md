# r64: BPF Clean Manifest, Batch, And Horizon Queue Stabilization

Date: 2026-07-04
Status: fixed; ready for next real-run gate in a new session

## Goal

- Close the r63 fail-closed blocker before spending another 3H-240s run.
- Remove clean-path work that was only hidden by skip/no-op decisions after
  full live scans.
- Reduce BPF generation installation cost and move H2 preparation out of the
  H0/H1 boundary pause.
- Keep failure semantics fail-closed: no full reload, full attach, serial
  fallback, live full scan, or hidden repair on the operational-clean path.

## Findings

- r63 proved H0 could consume H1 preloaded records, but failed at the H0/H1
  boundary because logical and physical horizon generation keys were mixed.
- H2 preparation was being completed during the H0/H1 maintenance boundary
  instead of being produced during H1 epochs.
- The old "skip" route still paid full cost in places: live map dumps, live
  diffs, target-generation cleanup scans, generation GC scans, and report/audit
  assumptions that treated no-op skip as proof.
- A generation-keyed BPF schema forced every generation to rewrite entries even
  when most content was inherited from the previous plan.
- Batch map writes changed reporting semantics: one batch command can represent
  many map writes/deletes, so audit fields must distinguish command count from
  entry count.
- Retiring H0 is not always immediately safe after H1 activates: later overlay
  generations may still depend on H0 as the physical root base.

## Changes

- Split BPF generation identity into explicit logical and physical horizon
  generation keys for BPF-owned readiness and transition checks.
- Added planned overlay support in the BPF dataplane through
  `generation_base_map`; current generation entries override base entries and
  tombstones explicitly block inheritance.
- Changed compiled BPF generation manifests to planned delta/tombstone overlays
  with `base_generation`, `root_base_generation`, and cumulative full-generation
  inputs for subsequent planning.
- Added manifest-only map loading for clean paths:
  - no `read_mpls_active_generation` probe
  - no live map dump/diff
  - no target-generation cleanup scan
  - no generation-GC scan
  - active-generation flip still happens last, or is deferred for preactivation
- Added `bpftool batch file` map update/delete execution for manifest-only work.
  This removes per-entry `bpftool` process overhead without falling back to
  per-entry writes in clean mode.
- Added `bpf_map_delete_manifest` and retired-generation cleanup work items.
  Cleanup deletes preplanned keys only and records dependency blockers when a
  generation is still used as an overlay base.
- Changed rolling-horizon BPF maintenance so future generation preload work is
  queued for runtime epochs instead of synchronously preloading every H2
  generation at the H0/H1 boundary.
- Removed the dead synchronous rolling preload loop to avoid a misleading second
  implementation path.
- Updated compact/runtime/profile reporting for:
  - manifest-only loader
  - manifest-only batch write
  - batch command count versus batched sub-update count
  - actual data write count versus delete count
  - queued preload records
  - retired cleanup queued/completed/blocked records
- Updated tests for planned overlay, root-base chaining, manifest-only batch
  writes, manifest-only delete manifests, and adjacent runtime/report contracts.

## Validation

- Syntax/import checks:

```bash
python -m py_compile \
  satellite/bpf_mpls_live.py \
  satellite/linux_mpls_runtime.py \
  satellite/apps/run_starlink_shell_experiment.py \
  satellite/apps/run_skyseed_validation_profile.py \
  satellite/test_bpf_mpls_live.py \
  satellite/test_runtime_validation_defaults.py
```

- BPF C compile through `compile_mpls_bpf` returned `0`.
- Focused BPF/runtime/profile regression:

```bash
python -m unittest \
  satellite.test_bpf_mpls_live \
  satellite.test_runtime_validation_defaults \
  satellite.test_skyseed_validation_profiles
```

Result: `Ran 257 tests ... OK`.

- BPF/runtime focused set:

```bash
python -m unittest satellite.test_bpf_mpls_live satellite.test_runtime_validation_defaults
```

Result: `Ran 194 tests ... OK`.

- Broad adjacent suite excluding the pre-existing planner return-path fixture
  issue:

```bash
python - <<'PY'
import pathlib, sys, unittest
root = pathlib.Path("satellite")
sys.path.insert(0, str(root.resolve()))
modules = [
    path.stem
    for path in sorted(root.glob("test_*.py"))
    if path.stem != "test_planner_return_path_intent"
]
suite = unittest.TestSuite()
loader = unittest.defaultTestLoader
for name in modules:
    suite.addTests(loader.loadTestsFromName(name))
result = unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
PY
```

Result: `Ran 602 tests ... OK`.

- Full `unittest discover` currently runs `607` tests and has `5` known
  failures in `test_planner_return_path_intent`, all with
  `PlannerError: zone service is not resolved for zone-a/ut101`. These are
  outside the r64 BPF/runtime path and were not introduced by the r64 changes.
- `git diff --check` passed on touched BPF/runtime/profile/test files.

## Remaining

- The next real run must measure whether delta overlays plus `bpftool batch
  file` reduce per-generation write cost enough. If not, the next optimization
  is a dedicated syscall batch writer gated on kernel support; do not add a
  silent fallback to per-entry writes.
- Runtime epoch work is queued by generation work item. If a single delta
  generation still exceeds the epoch budget, split a generation into smaller
  map-operation work chunks.
- Retired cleanup may be blocked while active/preloaded/queued generations
  depend on an older physical root base. This is correct; the next run should
  confirm blocked cleanup records are bounded and eventually clear when no
  longer referenced.
- The next real run must validate timing, not just contracts:
  H0 preload wall time, H1 epoch wall time, H2 queued preload progress, and
  retired cleanup cost.

## Watch Next

- Confirm r53/r55 contaminated artifacts are not reused.
- Confirm H0/H1 activation compares the physical BPF horizon generation key and
  consumes a real preloaded generation record.
- Confirm H2 preparation is queued and consumed during H1 epochs, not completed
  at the H0/H1 boundary.
- Confirm no manifest-only path performs live map dumps, live diffs, generation
  GC scans, target-generation cleanup scans, full reload, or per-entry fallback.
- Confirm report/profile labels include
  `RUNTIME_MANIFEST_ONLY_BATCH_MAP_WRITE_USED` for manifest-only batch writes
  and do not misclassify them as old live diff/no-op reuse.
- Confirm qdisc updates only occur for planned parameter updates that still
  require host qdisc; BPF-owned parameter map updates should not trigger host
  qdisc work.
- Confirm active generation flips only after required inactive map writes
  succeed, and cleanup runs only after activation with dependency checks.
