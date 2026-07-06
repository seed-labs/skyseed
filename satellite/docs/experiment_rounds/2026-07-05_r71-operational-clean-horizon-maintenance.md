# r71: Operational Clean Horizon Maintenance Diagnosis

Date: 2026-07-05
Status: stopped; diagnosed; next work should start with readiness contract fix

## Goal

- Run the next BPF-owned `operational_clean` runtime experiment after r64/r66.
- Validate clean manifest loading, planned overlay, batch map writes, queued
  preloads, per-epoch queue consumption, and retired cleanup behavior.
- Keep the run fail-closed: no full reload, full attach fallback, live full map
  dump/diff, generation-GC scan, target-generation cleanup scan, or hidden
  reconcile/report fallback on the clean path.

## Run

- Runtime output:
  `satellite/output/validation_profiles/s4-full-h3-240s-runtime-20260705-r71`
- Prepared input lineage:
  `satellite/output/validation_profiles/s4-full-h3-240s-prepare-20260704-r66`
- Final runtime outcome:
  `rolling_horizon_bpf_preload_base_compiled_inputs_missing:H1`
- The run exited fail-closed after cleanup and artifact finalization.

## Findings

- H0 runtime epoch completed successfully.
- H0 runtime epoch wall time was `3.264166s` for one epoch, not an average.
  The epoch reused prepared BPF readiness:
  - `bpf_reused_attach_count=236`
  - `bpf_delta_attach_count=0`
  - `bpf_full_attach_count=0`
  - `bpf_map_write_seconds=0.0`
  - `active_generation_flip_seconds=0.0`
- The expensive H0 setup remained front-loaded:
  - compose up: `1371.197907s`
  - host runtime prewarm: `816.220166s`
  - current horizon host install: `221.989278s`, `484/484` ops
  - pre-runtime BPF prepare: `223.951205s`
  - H0 inactive generation preload: `404.676296s`
- H1 rolling host additive prepare completed, but it was synchronous maintenance:
  - wall: `31.875807s`
  - slot count: `16`
  - created resources: `38`
  - host command count: `279`
  - `full_prepare_allowed=False`
  - `full_sweep=False`
  - `full_host_scan_skipped=True`
- H1 BPF attach capability prepare completed, but it was also synchronous
  maintenance:
  - wall: `125.441706s`
  - generation count: `25`
  - resident context count: `1032`
  - delta attach count: `250`
  - full attach count: `0`
  - runtime full generation reload count: `0`
  - `future_map_write_allowed=False`
  - `active_generation_flip_allowed=False`
  - `runtime_apply_skipped=True`
  - `active_generation_set=False`
  - `map_ready=False`
- The maintenance event explicitly classified this as blocking:
  `synchronous_next_horizon_host_and_attach_capability_prepare_in_current_loop`.
- H1 queued preload did not actually start. The runner failed before enqueueing
  work items because the base `compiled_runtime_inputs` were missing from the
  readiness locations consulted by the queue builder.
- H0 preloaded generation records were compacted to summaries; they showed
  successful inactive writes and deferred active flips, but did not preserve
  full `compiled_runtime_inputs` for the next base lookup.
- Manifest-only batch map write support exists in code and focused tests, but
  r71 did not reach a real H1 queued preload consumer, so the long run did not
  prove batch-map audit fields in live queued preload artifacts.

## Qdisc And Ping Notes

- The run did not repeat the earlier "skip all qdisc" failure mode.
- BPF tc/clsact attach was executed:
  - pre-runtime full attach count: `236`
  - H1 delta attach count: `250`
  - `tc_qdisc_cleanup_before_attach_wall_seconds=33.664407`
  - `bpf_attach_tc_qdisc_setup_wall_seconds=89.092163`
- ParameterUpdateBatch qdisc behavior was not validated because r71 completed
  only one runtime epoch and zero parameter epochs:
  - `parameter_qdisc_required_update_count=0`
  - `parameter_qdisc_command_count=0`
- No real ping-through proof was collected:
  - `ping_success=None`
  - pre/post transition connectivity records were skipped
  - `watcher_summary.enabled=False`
  - `bpf_ping_path_hit_count=0`
- A future bounded ping watcher is appropriate as background observation, not
  as a performance-critical safety gate for the clean-path timing run.

## Diagnosis

- The H0 runtime clean path is working enough to move heavy BPF work out of the
  first runtime epoch.
- The next-horizon physical preparation path is still in the epoch loop and is
  too expensive to block runtime scheduling.
- BPF map preload depends on physical interface materialization because
  compiled inputs contain real ifindex/MAC/out-ifindex values. A standalone BPF
  writer is therefore insufficient unless host resources and interface bindings
  are prepared first.
- The right abstraction is a horizon maintenance worker/module that owns
  "prepare next horizon" and "cleanup retired previous horizon" while the main
  runtime loop owns only current activation and active-generation flip.

## Planned Next Work

1. Fix the `compiled_runtime_inputs` readiness contract.
   - Preserve or reconstruct the current active/base generation manifest needed
     for H1 queued preload.
   - Do not treat compact summaries as full compiled inputs.
   - Keep missing base inputs fail-closed.
   - Add tests for missing-base failure and successful queued preload enqueue.

2. Extract horizon maintenance into a module, still called synchronously first.
   - Scope: host additive prepare, interface binding freeze, BPF attach
     capability prepare, inactive generation preload, and retired cleanup
     dependency reporting.
   - Forbidden: active-generation flip, future live host activation, full reload,
     full attach fallback, live full map dump/diff, hidden cleanup/reconcile.
   - Publish explicit ready/blocked/failed artifacts.

3. Move horizon maintenance to a background worker/subprocess.
   - H0 active starts H1 preparation.
   - H0 epochs continue without waiting for H1 maintenance.
   - H0->H1 boundary consumes a ready artifact or fails closed.
   - H1 active starts H2 preparation and retired H0 cleanup checks.
   - Active-generation flip remains in the main runtime boundary path.

## Watch Next

- Confirm H1 queued preload writes real manifest-only batch map updates and
  exposes batch command count versus batched sub-update count.
- Confirm planned overlay reduces generation write volume in live queued
  preload, not just in unit tests.
- Confirm retired cleanup records blocked when an older generation is still a
  root/base dependency.
- Confirm ParameterUpdateBatch qdisc updates execute when a parameter epoch is
  reached.
- Add a bounded background ping watcher as observational evidence without
  polluting epoch timing.
