# r106-r107: single-path cleanup

Date: 2026-07-09
Status: fixed

## Goal

- Recover from the r106 mislaunch through an obsolete capped short profile.
- Remove retired launch profiles, scripts, tests, and controller-backed runtime
  branches from the active surface instead of guarding or renaming them.
- Keep only the full operational-clean validation profile as the runnable
  validation entry.

## Findings

- r106 completed successfully but only executed four runtime transactions. It
  did not exercise horizon boundaries or process-worker handoff, so it is not
  valid H4 evidence.
- The active tree still exposed retired profile files, one-off live probe
  scripts, legacy Phase1 files, a controller-backed network runtime branch, and
  documentation that pointed back to deleted material.
- The direct runtime installer still needed `DirectHostRuntimeContext`; deleting
  the controller-backed branch initially exposed this as a cheap unit-test
  failure, before any expensive run.

## Changes

- Deleted retired validation profiles, one-off live probe scripts, the Phase1
  directory, controller-backed runtime sources, and legacy controller helper
  files.
- Removed the controller-backed installer branch from `network_runtime.py`; the
  active runtime path is now the direct Linux netns/veth/qdisc installer.
- Removed old short-profile and retired-route language from active README,
  file-map, test-bundle, runbook, and skill guidance.
- Kept the full validation profile contract: four default horizons, 240s
  horizons, no runtime epoch cap, and loop counts above the minimum allowed for
  future H5/H6 runs.

## Validation

- Active-surface scan found no retired launch/profile/probe/controller names
  outside historical docs, baselines, output, status logs, and vendored assets.
- `run_skyseed_validation_profile.py --list-profiles --format json` lists only
  `rich_s4_operational_clean_bpf`.
- Focused tests: 348 related profile, BPF/MPLS, runtime, and worker tests pass.
- Full unit suite: 709 tests pass.
- Full-profile dry-run uses `--loop-count 4`, `--horizon-seconds 240`, and no
  runtime epoch cap.
- `git diff --check` passes.

## Rerun Gate

- Previous failed run/stage: r106 invalid launch surface, not a runtime failure.
- Cheap check that now catches it: profile list contains one full profile; the
  profile contract rejects runtime epoch caps; active-surface scan has no
  retired launch or controller branch names.
- Adjacent lifecycle states covered: direct runtime context construction,
  qdisc lifecycle tests, worker maintenance tests, BPF/MPLS compile tests, and
  validation profile contract tests.
- Command source and run id: next live run should use
  `rich_s4_operational_clean_bpf` with a new run id, not r106 evidence.
- Forbidden-work expectations: no fallback profile, no controller-backed
  runtime branch, no capped runtime transaction shortcut, no hidden launch
  alternative.
- Remaining late-failure risks: live H-boundary process-worker behavior still
  needs a real compose/runtime run; r106 did not cover it.

## Remaining

- Historical docs, old status entries, baselines, and prior output still record
  past short/probe terminology as history. They are not active launch surfaces.
