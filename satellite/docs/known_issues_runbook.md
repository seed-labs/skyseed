# Skyseed Known Issues And Runbook

This is the durable runbook for recurring environment problems that show up
before or during real Skyseed experiments.

## Rule Zero

Run the project preflight before real Skyseed experiments, especially after:

- VM reboot
- interrupted `docker-compose up`
- failed `72/172` runtime attempts
- manual Docker cleanup

Command:

```bash
/home/seed/miniconda3/envs/seed310/bin/python satellite/apps/skyseed_preflight.py --format text
```

## Resume Checklist After VM Reboot Or New Codex Session

Treat the local environment as unknown until these checks pass:

0. Read this runbook before starting or resuming a real runtime. Do not rely on
   memory from an earlier Codex session or from before a VM reboot.
1. Confirm the branch and existing worktree state:

```bash
git branch --show-current
git status --short
```

2. Run the seed310 preflight:

```bash
PYTHONPATH=/home/seed/skyseed/satellite \
  /home/seed/miniconda3/envs/seed310/bin/python \
  satellite/apps/skyseed_preflight.py --format text
```

3. For preview-only work, confirm the intended artifact directory exists and
   contains the expected JSON summaries before using it as evidence.
4. For real-runtime work, do not continue from memory alone. Use
   `--host-prepare-policy auto` and `--preflight-policy require-clean`, then
   inspect the runner-written `preflight.json`, `host_prepare.json`,
   `runtime_lock_metadata.json`, and `post_run_preflight.json`.
5. Record new recurring failures in this file using this shape:
   `Symptoms`, `Detection`, `Fix`, and `Notes` or `Evidence`.

Before spending another long real run, run the durable session check. It is the
machine-readable handoff for a new Codex window and exposes only the current
operational-clean validation profile:

```bash
PYTHONPATH=/home/seed/skyseed/satellite \
  /home/seed/miniconda3/envs/seed310/bin/python \
  satellite/apps/skyseed_session_check.py --format text
```

For automation or for copying the exact active profile into a run note:

```bash
PYTHONPATH=/home/seed/skyseed/satellite \
  /home/seed/miniconda3/envs/seed310/bin/python \
  satellite/apps/skyseed_session_check.py \
  --format json \
  --write-report satellite/output/analysis/session_check_latest.json
```

Do not reconstruct the long-run command from chat memory. Use
`operational_iteration_profile.run_command`, replace only `<run_id>`, and
change performance-affecting flags only when the run objective explicitly says
it is diagnostic. The active handoff is the 4H `rich_s4_operational_clean_bpf`
validation profile: 240-second horizons, 10-second sample/event cadence,
`operational_clean`, `bpf_owned`, clean preflight, host prepare auto, orbit data
from `satellite/apps/visualization/planned_shell_orbit_data_s1_s5.json`, and
gateway data from `satellite/apps/visualization/starlink_gateways.json`.

Current reboot interpretation:

- `ready=True` with zero scoped containers, networks, direct-backend host
  interfaces, and lock files means the environment is clean enough for
  preview-only analysis.
- `host_mpls_not_ready` after reboot is expected until host MPLS preparation
  runs. It is a real-runtime blocker only if `--host-prepare-policy auto` or
  manual `prepare_host_mpls.py` cannot repair it. For the current
  `operational_clean` / `bpf_owned` iteration, session check suppresses this
  legacy host-runtime warning because the canonical command uses host prepare
  auto and the BPF clean path should be judged by scoped hygiene plus BPF
  contracts, not by pre-prepared MPLS state.
- `bridge_netfilter_passthrough_not_ready` should be corrected before real
  runs by host preparation so Linux bridge filtering does not perturb direct
  backend forwarding. It is likewise not shown as an operational-iteration
  warning for the current BPF-owned clean loop.

Write a JSON artifact when needed:

```bash
/home/seed/miniconda3/envs/seed310/bin/python satellite/apps/skyseed_preflight.py \
  --format json \
  --write-report satellite/output/preflight_latest.json
```

If a VM reboot drops you back onto the system `python3`, preflight now shows:

- `python_executable`
- `python_version`
- `preferred_seed310_selected`

Warnings such as `python_version_too_old_for_seedemu_runtime` or
`not_running_under_preferred_seed310_python` mean you should switch back to
`seed310` before real Skyseed experiments.

If an older local checkout is reviving through `PYTHONPATH`, preflight now also
shows:

- `legacy_seed_emulator_pythonpath_override_present`
- `legacy_seed_emulator_override_path`

This usually means an old shell init line such as
`export PYTHONPATH=~/seed-emulator` is overriding the editable
`seed-emulator-upstream-dev` dependency that Skyseed expects.

## Durable Real-Run Workflow

Real `run_starlink_shell_experiment.py --run-mode real-runtime` runs should now
use a fixed hygiene path instead of relying on session memory:

1. Host prepare:
   - ensure host MPLS readiness
   - force bridge-netfilter passthrough sysctls back to `0`
2. Scoped preflight:
   - detect stale Skyseed containers, networks, compose processes, host
     direct-backend interfaces, and runtime lock files before expensive work
3. Runtime lock:
   - mark an active real run so a crash or VM interruption leaves a detectable
     artifact for the next preflight
4. Scoped cleanup on exit:
   - unless `--keep-environment` is explicitly used, the runner should bring
     the Skyseed compose environment down even after failure or interruption
5. Post-run preflight:
   - confirm whether the environment returned to a clean state

## Expensive-Run Stabilization Gate

Before spending a long real-runtime run, treat the last failed run as training
data for a cheaper gate. Do not rerun only because the latest patch compiles.

Use this stabilization checklist:

1. Reconstruct the failure from artifacts:
   - compare bootstrap/H0 cost with rolling/H1 cost
   - count repeated operations by operation type and horizon/generation
   - identify whether time was useful work, repeated work, waiting, reporting,
     cleanup, or unknown
2. Classify the issue:
   - correctness: wrong state, missing readiness, unsafe future exposure,
     polluted artifact reuse, partial work marked ready
   - performance: serial bottleneck, unnecessary repetition, report blowup,
     work done at the wrong boundary
   - observability: reports or audits still describe the old behavior
3. Fix the flow, not the symptom:
   - do not use timeout-kill as a substitute for removing useless work
   - if host work is skipped because BPF owns it, advance the metadata that
     downstream validators compare
   - if work is deferred, prove who completes it before the consumer needs it
   - if work is parallelized, prove partial failure still fails closed and
     active/visible state remains last or deferred
4. Sweep adjacent contracts:
   - loader/backend result fields
   - runtime readiness checks
   - scheduler or rolling-horizon maintenance
   - compact artifacts and report summaries
   - profile/audit tools
   - tests that assert old labels, counts, or map names
5. Run layered checks before the long run:
   - compile/import checks for touched modules
   - focused unit tests for the changed path
   - failure-semantics tests
   - report/audit propagation tests
   - broader regression set around neighboring runtime paths

Operational-clean BPF runs must also pass the frozen runtime queue coverage
audit before compose starts. This audit is a preparation contract, not a
runtime participant:

- it consumes `normal_runtime_inputs.json`, `topology.json`, and the prepared
  route identity evidence when available
- it must not start Docker, call visualization, regenerate links, run live
  discovery, install routes, or repair missing input
- it blocks compose only for contract failures such as missing queue context, a
  BPF generation slot assignment that does not match the three-slot buffer
  contract, a prepared horizon segment count that does not match the requested
  long-run horizon count, or a runtime route consumer whose required prepared
  route identity has no producer
- it records timing and compact counts, but timing is evidence for optimization,
  not a fixed timeout gate

The current artifact is `runtime_queue_route_identity_coverage.json` for
compatibility with existing summaries, but its schema is the broader
`skyseed-runtime-queue-coverage-v1`. The standalone replay command is:

```bash
PYTHONPATH=/home/seed/skyseed/satellite \
  /home/seed/miniconda3/envs/seed310/bin/python \
  satellite/apps/audit_runtime_queue_coverage.py \
  --run-dir <run_dir-containing-normal_runtime_inputs.json> \
  --expected-horizon-count <loop_count>
```

When a new frozen lifecycle is added, extend
`satellite/runtime_queue_coverage_audit.py` and its tests before the next long
run. The intended pattern is one evolving producer/consumer contract over the
current clean path, not H3/H4-specific smoke scripts, capped launch profiles,
or retired shortcuts kept in the active tree.

For the 3H-240s BPF-owned rolling-horizon work, keep the gate focused on proof
categories rather than one run's exact field names:

- bootstrap/pre-runtime generation preload cost
- rolling-horizon maintenance cost
- count of real preloaded generations versus deferred or missing generations
- evidence that skipped work was intentionally owned elsewhere
- evidence that preactivation map writes used the intended execution strategy
- evidence that worker preactivation consumes frozen compiled inputs and
  prepared attach-capability proof instead of doing live discovery, binding,
  full scans, or repeated attach guards
- evidence that active generation changed only after all required data was ready
- report/audit labels that match the current implementation rather than an old
  full-serial preload path

The run is not considered a clean validation if H1 reaches activation with
missing preloaded generation records, if active generation flips before all
required inactive map writes succeed, or if a report/audit still interprets the
new path using obsolete assumptions.

Preloaded-generation handoff failures must block another expensive 3H rerun
until the cheap gate covers the whole lifecycle, not only the failing horizon.
At minimum, tests or a targeted replay must prove:

- The first consumer after bootstrap/current-horizon activation consumes or
  establishes the expected active generation without erasing remaining
  current-horizon preloaded records.
- The immediate successor epoch after any successful preloaded/active
  generation transition consumes the next required preloaded generation rather
  than reusing existing readiness as `existing_readiness_reused_no_prepare`.
- Consumed-record removal advances `current_source_state_id` while preserving
  later records whose source is the just-activated target state.
- A frozen preloaded record is authoritative only when generation, horizon,
  source state, and epoch identity match; source-state or epoch-identity
  mismatches still fail closed.
- Rolling-horizon ready artifacts are merged at the runtime boundary, and at
  least one successor epoch after that boundary consumes segment-scoped records
  without live intent recomputation, full reload, fallback, or broad discovery.

Do not start a long run after a narrow BPF handoff patch until this bootstrap
and rolling lifecycle gate passes alongside compile checks, focused runtime
validation tests, qdisc/report contract tests, `git diff --check`, and
`skyseed_session_check.py --format text`.

Before launching any 3H operational rerun, write or update the active
experiment-round note with a `Rerun Gate` section containing:

- previous failed run id and exact failure stage
- the cheap test, targeted replay, or artifact audit that would catch that
  failure now
- adjacent lifecycle states covered, including first consumer and immediate
  successor after a consumed/prepared record
- exact command source, normally
  `operational_iteration_profile.run_command`, and the new run id
- forbidden-work expectations and the artifact fields that will prove them
- remaining risks; if a risk can plausibly fail late, the run remains blocked

Keep exact field names, thresholds, and run-specific expectations in the active
run note, session-check output, or tests. Update this runbook when a repeated
failure reveals a durable category of risk; remove or generalize details that
were only true for one implementation.

## Operational Clean Runtime Doctrine

The `operational_clean` runtime is a contract, not a temporary collection of
flags. Treat it as the only valid runtime route for the current BPF-owned
operational iteration.

Clean runtime means the live epoch boundary performs only the network work that
must become visible at that boundary:

- direct host physical mutation for the current epoch
- current-epoch qdisc batches
- necessary UT edge route/IP work for the current epoch
- BPF active generation flip after the required inactive data is ready
- fail-closed proof for the operation being published

Everything else must be prepared before the boundary, performed after the data
plane is ready, or recorded asynchronously:

- future-horizon host plans, attach capability, and inactive BPF generations
  are prepared from frozen/prepared artifacts and consumed only at the boundary
- reporting, timing summaries, diagnostics, and observations must not block the
  data-plane update unless they are the fail-closed proof for the current
  operation
- ping or packet observers are diagnostic; failures are raw observations unless
  the run objective explicitly promotes them to a gate

Absolute isolation is part of the contract. Legacy controller-backed,
Linux-MPLS, mini-host validation, full reload, full scan, live dump/diff, hidden
reconcile paths, and superseded maintenance routes must not remain as visible
branches inside the active clean runner/profile. Delete or move retired paths
out of the active implementation instead of keeping them as guarded fallbacks,
"safety" routes, or optional modes. If a path should never be selected, it
should not be a branch a future edit can accidentally revive.

Apply the same rule to launch profiles and tests. Active validation profiles
must represent the current clean objective, not old short, preview, capped-run,
or retired-route shortcuts. Do not keep short-run profiles or tests that only
prove a retired shortcut still launches. If a cheaper validation is needed,
define a new one from the current objective and explicitly state what lifecycle
boundary it covers.

For major refactors, deletion is the stabilization mechanism. Retired modules,
duplicate implementations, compatibility wrappers, temporary scripts, and
obsolete tests should be removed from the active tree, not renamed, grouped
together, documented as "do not use", or left behind behind a selector. The
goal is that current source searches expose one intended path and current tests
exercise that path directly.

The clean path should have one authoritative source of truth for each state:

- prepared/frozen artifacts for future work
- pending records for work not yet consumed
- active state for the currently visible runtime
- retired state for cleanup
- historical evidence only for reporting and post-run analysis

Large reports must follow the same ownership rule. If a record body already has
an owner artifact, all other reports should carry only `path/hash/count/summary`
references. Do not write the same prepared/frozen payload twice just to give it
two names; use an alias or manifest reference, and break that alias only before
writing genuinely different content. Runtime reports, timing summaries, ready
artifacts, and handoff manifests must not embed full prepared bundles,
pre-runtime BPF prepare bodies, compiled map bodies, per-update payloads, or
generation write-cost audit bodies unless that file is the single owner of the
data.

The BPF generation map lifecycle is a rolling three-slot contract:

- previous horizon generations are retired and cleaned by explicit manifest
  deletes once no active, pending, queued, or ready record still references
  them
- current horizon generations are active/visible and must not be deleted or
  overwritten
- next horizon generations are prepared in inactive slots from frozen inputs
  and become visible only at the runtime boundary

H0 is the bootstrap exception: it has no previous horizon to clean. From H1
onward, horizon maintenance should preserve this previous/current/next state so
longer loops such as H4 do not lose track of physical slot ownership. A ready
artifact for a future horizon must prove both that required inactive generation
writes succeeded and that any physical slots it needs from retired generations
are clean or still safely retained. Slot reuse based only on historical evidence
or a broad live map scan is not a clean fallback.

Do not add global live guards that compare consumed historical sets against
mutable runtime queues. If a guard is needed, first classify which state class
it reads and keep live checks scoped to the current operation or pending
records. This avoids self-inflicted gates that pass only before normal
consumption and then block the valid lifecycle later.

## Current Canonical Command Index

The stable real-runtime path is the 4H
`rich_s4_operational_clean_bpf` validation profile. It consumes the
visualization planned-shell orbit catalog and visualization gateway catalog,
then applies the rich S4 G/Z UT and zone semantics through the BPF-owned clean
runtime.

Use these commands as the normal entry points:

- Preflight:

```bash
PYTHONPATH=/home/seed/skyseed/satellite \
  /home/seed/miniconda3/envs/seed310/bin/python \
  satellite/apps/skyseed_preflight.py --format text
```

- New-session handoff and canonical run command:

```bash
PYTHONPATH=/home/seed/skyseed/satellite \
  /home/seed/miniconda3/envs/seed310/bin/python \
  satellite/apps/skyseed_session_check.py --format text
```

Use `operational_iteration_profile.run_command` from the JSON form when copying
the exact command into a run note. Replace only `<run_id>` unless the run note
explicitly says this is a diagnostic variant. It is acceptable for this profile
to regenerate and freeze a new prepared bundle each round; the docs in
`satellite/docs/experiment_rounds/` carry the durable round-to-round evidence.
- Preview-only zone comparison:
  use `satellite/apps/build_starlink_shell_preview.py` or
  `satellite/apps/build_starlink_shell_preview_matrix.py`; write artifacts
  under `satellite/output/` and do not start real runtime.
- Analysis / plot bundle extraction:
  use `satellite/apps/inspect_temporal_run.py`,
  `satellite/apps/compare_temporal_runs.py`,
  `satellite/apps/summarize_starlink_long_run.py`, and, for the current 6-zone
  figure set, `satellite/apps/generate_gz_6zone_2h_figures.py` after the run
  has finalized.

24-zone and 32-zone catalogs are preview-only. Treat old coarse-planner
comparisons, deterministic zone-catalog generation, TLE fetching, and
goal-specific inspectors as analysis or experiment support tools, not as the
canonical startup route.

Key artifacts written by the runner:

- `host_prepare.json`
- `preflight.json`
- `post_run_preflight.json`
- `runtime_lock_metadata.json`
- `docker_compose.json`
- `run_manifest.json`

For live monitoring without waiting for final manifests, use:

```bash
/home/seed/miniconda3/envs/seed310/bin/python satellite/apps/inspect_temporal_run.py \
  satellite/output/starlink_shell_experiments/<run_id> \
  --format text
```

This command summarizes:

- whether progress is still advancing
- whether live runtime is already complete and rollback is the only remaining phase
- current `RuntimeEpoch` / `ParameterEpoch` lag and wall-time statistics from `events.jsonl`
- whether finalized artifacts already exist or only checkpoint artifacts are present

To compare multiple completed runs, use:

```bash
/home/seed/miniconda3/envs/seed310/bin/python satellite/apps/compare_temporal_runs.py \
  satellite/output/starlink_shell_experiments/<run_a> \
  satellite/output/starlink_shell_experiments/<run_b> \
  --format text
```

This is the preferred durable way to compare:

- `10s` vs `5s` slice behavior
- `RuntimeEpoch` / `ParameterEpoch` counts
- activation-lag averages
- steady transition-wall averages
- steady host-apply averages
- noop `ParameterEpoch` incidence

Recommended real-run mode for larger experiments:

```bash
/home/seed/miniconda3/envs/seed310/bin/python satellite/apps/run_starlink_shell_experiment.py \
  ... \
  --run-mode real-runtime \
  --host-prepare-policy auto \
  --preflight-policy require-clean
```

`--host-prepare-policy auto` should be treated as the normal baseline, not as
an ad-hoc recovery flag.

## Current Operational-Clean Validation Route

The only active long-run handoff is the session-check
`operational_iteration_profile.run_command`. It points to
`run_skyseed_validation_profile.py --profile rich_s4_operational_clean_bpf`.

The profile owns the real command shape, including the 4H horizon count, BPF
clean runtime, disabled runtime epoch caps, and visualization-sourced orbit and
gateway inputs. Do not keep alternate smoke, capped, 1-shell, or retired
catalog commands in the active runbook. If a smaller diagnostic is needed,
define it from the current profile and state the diagnostic objective in that
round's note.

The current profile's gateway binding, visualization physical-link contract,
and temporary serviceability selection are recorded in the corresponding
experiment-round note and tests. Do not promote those phase-specific choices
into a permanent runbook gate unless later runs prove they are durable project
semantics.

## G/Z Zone Planning And Diff Artifacts

Before changing runtime apply code or starting a new real long run for G/Z
performance, inspect the preview planning artifacts. They are the durable
evidence for whether the workload is zone/FEC-shaped or has accidentally
become per-UT/per-flow again:

- `zone_model.json`: zone geometry, return prefixes, membership, and active
  gateway policy metadata.
- `zone_anchor_schedule.json`: per slice `zone -> responsible satellite`,
  quality score, coverage margin, gateway path hops, change reason, and whether
  the anchor changed.
- `zone_anchor_quality_summary.json`: per-zone sample count, handover count,
  unresolved count, coverage margin min/avg, and anchor change reasons.
- `gz_fec_plan.json`: per snapshot G forward FECs and G/Z return FECs grouped
  by gateway class, zone, responsible satellite, labels, path, pop point, and
  affected nodes.
- `gz_update_diff_summary.json`: per event added/removed/changed/skipped FEC
  groups, changed zones, affected nodes, and estimated LFIB/route operation
  items.
- `zone_availability_analysis.json`: per-role and per-zone resolved/unresolved
  samples, required-zone covered windows, dwell distributions, and short dwell
  counts.

Interpretation rules:

- The current zone anchor generator is `zone_anchor_quality_generator_v1`.
  It chooses the responsible satellite from zone-level geometry using zone
  center elevation/coverage margin, gateway-hop cost, and hysteresis. For an
  active `required_service` or `optional_service` zone with UTs, the selected
  anchor must also be visible to the zone UTs. This is an access-edge
  consistency constraint; it must not reintroduce per-UT MPLS state into the
  satellite core.
- Only active service zones install G/Z FEC. `observation` zones may be
  resolved or unresolved for coverage analysis, but they do not install
  backbone G/Z return FEC and must not block a representative runtime window.
- Candidate anchors must clear the configured coverage-loss margin above the
  minimum elevation. A center-only pass that is only barely above the minimum
  elevation can leave some zone-member UTs without an access attachment, so
  treat near-horizon zone anchors as unresolved instead of letting planner
  input become inconsistent.
- Planner flow contexts may still appear per UT because edge delivery and
  ping/validation need UT identity. Do not use raw flow-context count alone as
  evidence of satellite-core scaling. Use `gz_fec_plan.json` and confirm
  `planning_scope.backbone_state_scales_with_ut_count=false`.
- Initial cold materialization will show added FEC groups. Steady RuntimeEpoch
  analysis should focus on later `gz_update_diff_summary.json` rows and check
  `skipped_unchanged_fec_group_count`, `changed_gz_return_group_count`, and
  `affected_node_count`.
- If `affected_nodes` approaches the whole satellite core for ordinary steady
  events, first check path-hop distribution, zone-anchor churn, and FEC target
  churn before changing G/Z semantics.

The preview builder writes these artifacts into the same config directory as
`planner_preview.json` and `temporal_plan.json`, and the real runner copies them
into the run directory plus `prepared_update_bundle.json` / generated artifact
manifests. A quick non-real smoke route after code changes is:

```bash
PYTHONPATH=satellite:satellite/apps \
  /home/seed/miniconda3/envs/seed310/bin/python \
  satellite/apps/build_starlink_shell_preview.py \
  --orbit-input-mode planned-shell \
  --planned-shell-id S5 \
  --max-satellites 24 \
  --max-uts 2 \
  --max-gateways 2 \
  --ground-placement catalog \
  --slice-count 2 \
  --step-seconds 60 \
  --skip-seed-render \
  --config-dir /tmp/skyseed-gz-preview-smoke/config \
  --output-dir /tmp/skyseed-gz-preview-smoke/out \
  --format json
```

This smoke is only an artifact/schema check. It is not a replacement for the
requested 72-satellite or 1-shell preview comparison, and it is not real-runtime
validation.

2026-06-11 preview comparison evidence:

- `satellite/output/gz_zone_fec_preview_compare_20260611_0551/`
- Compared `72` satellites and full `S5` `172` satellites over a `7200s`
  preview horizon with `10s` event evaluation, `12UT/6GW`, explicit valid6
  catalogs, explicit 6-zone catalog, and `--ground-placement catalog`.
- No real runtime was started.
- The comparison used `comparison_summary.json` plus the five G/Z planning
  artifacts in each config directory.
- The diff planner skipped unchanged FEC groups and did not produce any event
  touching more than 50% of satellites. Full-reinstall baseline estimates were
  used because no previous coarse planner artifact exposes the same FEC diff
  model.
- Remaining caveat before representative real runtime: full `1-shell` still
  had `zone-south` unresolved for a large part of the sampled two-hour window,
  so choose a shorter covered window or improve the zone-anchor availability
  algorithm before treating this as ready for a broad real-runtime run.
- Follow-up availability analysis:
  `satellite/output/gz_zone_fec_preview_compare_20260611_0551/zone_availability_analysis_1shell172.json`.
  In that preview, all six zones are continuously resolved from relative
  `3790s` through `7200s`; a low-update `600s` smoke window is relative
  `6340s..6940s` / absolute `2026-06-11T11:43:00Z..11:53:00Z`.
  `zone-south` unresolved samples are mostly all candidates below minimum
  elevation, not UT attach mismatch; splitting the existing two south UTs is
  unlikely to fix coverage because they are only about `5.6-6.1km` from the
  zone center.

2026-06-11 deterministic zone catalog Stage 2 preview evidence:

- `satellite/output/zone_catalog_stage2_preview_20260611_0712/`
- Summary:
  `satellite/output/zone_catalog_stage2_preview_20260611_0712/stage2_zone_comparison_summary.json`
- Compared full `S5` `172` satellites over a `3600s` preview horizon with
  `10s` event evaluation, `6-zone` metadata baseline, deterministic `24-zone`,
  and deterministic `32-zone` catalogs. No real runtime was started.
- Resource cost was acceptable on the resized VM but preview artifacts are
  large: about `303 MiB / 85s / 1.46 GiB RSS` for 6-zone,
  `841 MiB / 295s / 3.17 GiB RSS` for 24-zone, and
  `1206 MiB / 451s / 4.74 GiB RSS` for 32-zone.
- G/Z core state still scales with active service zones, not UT count:
  `12UT -> max 5 return FEC`, `20UT -> max 12 return FEC`, and
  `26UT -> max 16 return FEC`; no FEC ID contains a UT ID.
- No steady update touched more than `50%` of satellites, and no affected node
  was `None` or outside the generated topology.
- The 6-zone metadata baseline is still suitable for a short covered real
  runtime smoke: five required zones are resolved for the whole sampled window;
  the optional south zone is unresolved and does not block runtime.
- The 24/32-zone catalogs are not ready for representative real runtime as-is:
  `zone-north-america-central-01` and `zone-south-asia-01` are unresolved for
  the whole sampled hour; `zone-middle-east-01`, `zone-europe-west-01`, and in
  32-zone `zone-southeast-asia-01` also create required-zone gaps. Sampled
  rejected candidate reasons are below-min-elevation, not UT attach mismatch.
- The planner diff is path/FEC-scoped, but 24/32-zone runs still show broad
  multi-zone churn events where most active service FEC groups change at the
  same event time. Treat these as planner/catalog optimization work
  (role/geometry/start-window/gateway path/hysteresis), not as evidence that
  G/Z semantics should be changed.

## Long-Run Timing Policy

For long event-time `1-shell` runs, keep the timing model split into three
separate buckets:

1. Pre-runtime bootstrap:
   - compose build / compose up
   - container readiness
   - host prewarm / cold prepare / cold reconcile
2. Live runtime execution:
   - scheduler wait
   - per-epoch activation lag
   - `RuntimeEpoch` host apply / node-local apply
   - `ParameterEpoch` qdisc / metadata / dirty reconcile / targeted validation
3. Post-run teardown:
   - node-local rollback
   - host rollback
   - host artifact cleanup
   - post-run preflight

Only bucket 2 should be used to judge whether a `600s` or `1200s`
event-time run is keeping up with its own update schedule. Slow rollback or
cleanup after the final epoch is a reuse and throughput problem, not direct
evidence that live update latency was too high.

For the current `1-shell` direct-backend path, `run_manifest.json` still
appears late, but `timing.json`, `timing.csv`, `real_runtime_report.json`,
and `temporal_audit.json` now checkpoint during live execution. During a
successful long run, the live epoch queue may already be complete while the
runner is still inside rollback/cleanup.

Interpret the timing artifacts like this:

- `timing.json.finalized=true` means the run reached final timing export.
- `timing.json.finalized=false` means the file is an in-progress checkpoint,
  but the epoch rows and timing summary are still valid up to the last written
  epoch.
- If an interrupted run reaches final export with `timing.json.finalized=true`
  but `timing.json.rows=[]` / empty `timing.csv`, check
  `real_runtime_report.json.iterations` before declaring the timing lost. The
  2026-06-11 interrupted `1h` attempt preserved `29` live iterations there even
  though final timing export briefly wrote an empty table; the runner now falls
  back to that live checkpoint for final timing export.
- `real_runtime_report.json.finalized=false` means the live runtime checkpoint
  is current but rollback/post-run hygiene is not final yet.
- `temporal_audit.json.finalized=false` means the safety audit is only the
  in-progress live/runtime view and should not yet be treated as final cleanup
  evidence.

When that happens, inspect:

- `runtime_progress.json`
- `events.jsonl`

If `runtime_progress.json` shows a phase such as `runtime_rollback:*`,
`runtime_epochs_completed` / `parameter_epochs_completed` are already at their
final values, and `events.jsonl` already contains late
`runtime_epoch_result`, `parameter_epoch_result`, or
`horizon_replenishment_completed` records, treat the live runtime itself as
completed and wait for teardown separately.

After `runtime_progress.json` reaches `runtime_rollback_completed`, the runner
may still be inside `docker-compose down --remove-orphans -v`. This is teardown,
not live update latency. Check the child process and container count before
intervening:

```bash
pgrep -af '<run_id>|run_starlink_shell_experiment.py|docker-compose down'
docker ps --format '{{.Names}}' | wc -l
```

If the container count is decreasing, keep waiting and record the teardown
duration separately. Only perform manual cleanup after the runner exits, or
after the compose teardown is demonstrably stuck and the run directory has
already checkpointed `timing.json`, `real_runtime_report.json`, and
`runtime_progress.json`.

## Long-Run Resource Gate And Validation Profile

Before starting a long real-runtime run, record host resource headroom. Do this
even when the functional preflight is clean, because a run can be functionally
ready but still too memory constrained for a representative long run.

Minimum resource snapshot:

```bash
free -h
df -h /home/seed/skyseed
du -sh satellite/output/starlink_shell_experiments/<run_id> 2>/dev/null || true
/home/seed/miniconda3/envs/seed310/bin/python satellite/apps/skyseed_preflight.py --format text
```

Record the observed `Mem: available`, `Swap: free`, filesystem `Avail`, and
the run directory size in the run notes or final report. During a long run,
periodically re-check:

- `runtime_progress.json.self_process.vmrss_kib`
- `resource_usage.jsonl` for phase-by-phase resource samples
- `du -sh satellite/output/starlink_shell_experiments/<run_id>`
- `free -h`
- `df -h /home/seed/skyseed`

Treat exhausted swap, rapidly shrinking available memory, or runaway artifact
growth as a resource gate failure. Do not start a two-hour run immediately
after a diagnostic run if containers, direct-backend interfaces, or swap
pressure are still present; wait for cleanup and re-run the resource snapshot.
If `SwapFree` repeatedly reaches `0` and `MemAvailable` falls below roughly
`500 MiB`, treat the run as being inside the host resource danger zone even if
epoch progress is still advancing. Continue only when the purpose is explicitly
to characterize resource limits; otherwise stop cleanly, preserve artifacts,
and reduce scale or add host memory before retrying.

New runs write `resource_usage.jsonl` alongside `runtime_progress.json`. Each
row records the sample reason (`progress_update` or `heartbeat`), current
phase, epoch context, process RSS, system `/proc/meminfo` fields, workspace
disk free bytes, run directory size bytes, Docker runtime counts, and
direct-backend interface count. Use it like timing artifacts: compare resource
levels at compose startup, prewarm, epoch apply/validation, rollback, and
post-run preflight boundaries before optimizing or changing long-run limits.

Keep diagnostic validation mode separate from representative long-run mode.
Strict settings such as:

```bash
--runtime-epoch-validation-interval 1
--parameter-epoch-validation-interval 1
```

are useful when isolating a failure, but they add per-epoch probe and recovery
work and can inflate activation lag, rollback time, and swap pressure. They are
not the default performance profile for a long run. A representative long run
should keep the normal startup hygiene, ping watcher, final/post-run checks,
and required safety validation, while avoiding unnecessary per-epoch diagnostic
probes unless the run objective explicitly asks for them.

Evidence from 2026-06-11:

- `short-1shell-12ut6gw-xtwait-20260611` used strict per-epoch validation for
  fault isolation after an xtables-lock fix.
- The run kept progressing and exported `25/25` epoch records with
  `ping_success=true`, but activation lag grew into the hundreds of seconds
  and swap dropped to near zero while containers were active.
- This indicates a resource and validation-load boundary, not a new command
  syntax failure. Use the result to tune the long-run profile and resource
  gate, not as the representative long-run timing baseline.
- `long-1shell-12ut6gw-2h-resourceprofile-host-20260611` used the
  representative long-run validation profile, not strict per-epoch validation.
  It progressed through live rolling replenishment to `runtime_epochs_completed=64`
  and `parameter_epochs_completed=78`, with watcher pings recovering after
  apply-window transients, but the kernel killed the runner:
  `Out of memory: Killed process 1356303 (python)`.
- That run showed `resource_usage.jsonl` reaching `SwapFree=0 KiB` and
  `MemAvailable` around `446 MiB` before the OOM. The final clean recovery
  required `docker-compose down`, deleting `193` residual `sbr*` direct-backend
  interfaces, deleting the stale active runtime lock for PID `1356303`, and
  rerunning preflight until all warnings were gone.

## Rollback Validation Policy

Do not require every successful long run to wait for a full rollback/cleanup
cycle if that cycle is already known-good and no relevant rollback, cleanup,
or environment-management code changed.

Use this policy:

1. Keep a recent fully clean pass as rollback/cleanup correctness evidence.
2. If code changed in runtime rollback, host cleanup, scoped compose cleanup,
   preflight, lock handling, or related environment lifecycle logic, require a
   fresh full rollback + post-run preflight pass again.
3. If code changed only in live update behavior and rollback/cleanup code did
   not change, prioritize live runtime evidence first.
4. If a long run finishes its epoch queue but teardown becomes disproportionately
   expensive, it is acceptable to stop the environment, clean it with the
   normal scoped workflow, and preserve the run as a partial teardown artifact
   rather than treating the live runtime itself as failed.

The current clean rollback/cleanup evidence for long `1-shell` event-time work
includes `gz-z6metadata-2h-gated-20260611-0824` and
`1shell-long-600-replenishfix`, which completed with:

- `real_apply_success=true`
- `ping_success=true`
- `rollback_completed=true`
- temporal audit pass
- post-run preflight clean

For `gz-z6metadata-2h-gated-20260611-0824`, keep the teardown cost separate
from steady RuntimeEpoch timing: node-local rollback was `1410.551s`, host
rollback was `1155.177s` across `600` host rollback records, and host artifact
cleanup was `98.723s`. Resource headroom was healthy on the larger VM
(`swap_used=0`, max runner RSS about `1091 MiB`, final run directory about
`967 MiB`).

Use that run as the baseline until rollback/cleanup-related code changes.

## Known Issue: Full-Shell Host Rollback Is Linear And Slow

### Symptoms

- A bounded full-shell event-time run completes the live epoch queue with
  `real_apply_success=true`, `ping_success=true`, and no temporal violation,
  but teardown dominates wall time.
- `runtime_progress.json` stays in `runtime_rollback:host` for many minutes
  while `completed_host_rollbacks` advances one item at a time.
- The child commands are ordinary host cleanup/restoration operations such as
  `tc qdisc replace`, `tc qdisc del`, `ip link set`, or `nsenter ... ip addr`.

### Detection

Inspect:

```bash
/home/seed/miniconda3/envs/seed310/bin/python satellite/apps/inspect_temporal_run.py \
  satellite/output/starlink_shell_experiments/<run_id> \
  --format text
```

For the 2026-06-10 full-shell G/Z diagnostic
`gz-long-1shell-240s-diagnose-20260610-1055`, the live `240s` queue passed
with `13` RuntimeEpochs and `12` ParameterEpochs, but host rollback processed
`25` host history entries at roughly `41-45s` each before post-run preflight
finally returned clean.

For the 2026-06-11 gated 6-zone full-shell run
`gz-z6metadata-2h-gated-20260611-0824`, the live `7200s` queue passed with
`325` RuntimeEpochs and `275` ParameterEpochs. Final cleanup succeeded, but
teardown was still the dominant post-live cost: node-local rollback handled
`325` sessions in `1410.551s`, host rollback handled `600` records in
`1155.177s`, and host artifact cleanup took `98.723s`.

### Fix

Do not treat this as a G/Z return-semantics failure. Treat it as a runtime
throughput issue before attempting longer full-shell validation.

Next narrow optimization targets:

- coalesce duplicate host rollback history entries before teardown;
- skip no-op qdisc/link restoration when the final desired state already
  matches the current host state;
- batch independent host cleanup commands where rollback ordering allows it.

### Evidence

- Live dataplane result:
  `output/starlink_shell_experiments/gz-long-1shell-240s-diagnose-20260610-1055`
- G/Z semantic artifact:
  `output/gz_scale_12ut6gw_20260610_1035/gz_return_semantics_long_240s_diagnose_20260610_1055.json`
- Post-run preflight was clean, with no compose processes, no scoped
  containers/networks, and no direct-backend host interfaces left behind.

## Known Issue: Full-Shell One-Hour Runs Can OOM Before Final Rollback

### Symptoms

- A full-shell G/Z long run keeps making live event-time progress, keeps ping
  validation green, and does not emit temporal violations, but the runner
  process is killed before final cleanup.
- The shell exits with status `137` or prints only `Killed`; final stdout JSON
  may be empty because the process died before final export.
- `real_runtime_report.json`, `timing.csv`, `timing.json`, `events.jsonl`, and
  `runtime_progress.json` may still contain valid checkpoint evidence up to the
  last completed epoch, but `finalized=false`, `rollback_completed=false`, and
  `post_run_preflight.json` will be absent or incomplete.

### Detection

Check kernel logs:

```bash
dmesg -T | grep -E 'oom-kill|Out of memory|Killed process' | tail -20
```

The 2026-06-10 one-hour G/Z attempt
`gz-long-1shell-60m-real-rollbackopt-postreboot-20260610-1404` was killed at
event-time `3510s/3600s`:

- `Out of memory: Killed process 6016 (python)`
- `anon-rss:1791032kB`
- checkpoint RSS in `runtime_progress.json`: `1187752 KiB`
- completed before kill: `122` RuntimeEpochs and `196` ParameterEpochs
- live replenishment completed: `13` future batches
- recorded epoch ping results before kill: `318/318` success
- temporal checkpoint still had no future active-link or future forwarding-state
  violation, but was not finalized because rollback never ran

### Safe Recovery

Preserve the run directory first; it is the evidence. Then clean only scoped
runtime residue:

```bash
docker-compose -f satellite/output/starlink_shell_experiments/<run_id>/seed_output/docker-compose.yml \
  down --remove-orphans

for path in /sys/class/net/sbr* /sys/class/net/vhs* /sys/class/net/vtm*; do
  [ -e "$path" ] || continue
  iface=$(basename "$path")
  [[ "$iface" =~ ^(sbr|vhs|vtm)[0-9a-f]{11}$ ]] || continue
  sudo -n ip link del "$iface"
done

rm -f satellite/output/skyseed_real_runtime_active.lock

/home/seed/miniconda3/envs/seed310/bin/python satellite/apps/skyseed_preflight.py --format json
```

If preflight still reports `direct_backend_host_interfaces_present`, inspect
`ip -o link show` and remove only Skyseed-prefixed `sbr*`, `vhs*`, `ga-*`, or
`ua-*` artifacts. Do not delete historical output directories as part of this
cleanup.

### Mitigation / Current Status

Do not rerun the one-hour or multi-hour full-shell path blindly after an OOM.
First preserve the failed run directory, clean scoped residue, and confirm
preflight. The narrow mitigation added after the first OOM was memory/report
compaction for long continuous event-time runs:

- avoid retaining every full epoch execution object in the in-process
  `real_runtime_report`;
- stream or summarize replenished segment artifacts after writing them to disk;
- keep bounded rolling timing/connectivity/G/Z summaries in memory while
  preserving detailed JSON on disk.

The 2026-06-10 post-mitigation run
`gz-long-1shell-60m-real-contextprune-20260610-1655` completed the same
one-hour full-shell G/Z path with:

- `126` RuntimeEpochs and `201` ParameterEpochs;
- `13` live horizon replenishments;
- `738/738` post-transition ping checks plus final ping success;
- rollback success and clean post-run preflight;
- no future active-link or future forwarding-state violation;
- max sampled RSS about `474 MiB`, with no kernel OOM evidence.

The mitigation therefore appears effective for one-hour validation. For
multi-hour validation, continue sampling RSS and artifact size growth. If memory
growth returns, inspect retained `applied_sessions`, node-local rollback
history, and any newly embedded full epoch details before changing G/Z
semantics.

This is not a G/Z return-semantics failure. In the failed one-hour attempt, the
separate G/Z inspector still passed with `3` shared return groups, shared
backbone return state `6` versus legacy `36`, `gateway_ut32_return_route_count=0`
in the shared model, and `ut_mpls_egress_count=0`.

## Known Issue: VM Reboot Or Failed Run Leaves Stale Skyseed State

### Symptoms

- `72` or later experiments fail before runtime begins
- `docker-compose up` times out or hangs in service creation
- reuse-backed runs incorrectly start from a dirty environment
- direct-backend apply finds unexpected host bridges or veths
- a later run behaves differently after an earlier failed run

### Detection

Use the preflight and look for:

- `seed_output_compose_resources_present`
- `named_skyseed_networks_present`
- `direct_backend_host_interfaces_present`
- `skyseed_compose_or_runtime_processes_present`
- `runtime_lock_files_present`
- `host_mpls_not_ready` only for legacy/MPLS runtime readiness; do not treat it
  as a current BPF-owned operational-clean iteration blocker when the session
  check reports `operational_iteration_ready=True`

The preflight also records:

- Skyseed container count
- Skyseed network count
- network attachment count
- active `docker-compose` / Skyseed runtime processes
- host direct-backend bridge and veth residue
- host MPLS readiness
- bridge-netfilter passthrough readiness
- safe cleanup suggestions scoped to Skyseed artifacts only

## Safe Cleanup Strategy

Do not run broad Docker cleanup like `docker system prune -a` for this issue.

Preferred order:

1. Stop stale Skyseed compose/runtime processes only.
2. Remove Skyseed compose project containers/networks only.
3. Remove named Skyseed slot/runtime networks only.
4. Remove direct-backend host interfaces only.
5. Remove Skyseed lock/pid files only.
6. Re-run preflight.

The preflight prints exact cleanup suggestions for the resources it detected.

## Known Issue: Ghost Docker Endpoints Keep `seed_output` Networks Alive

### Symptoms

- preflight still reports `seed_output` compose networks even though compose
  containers are already gone
- `docker network rm ...` fails with `has active endpoints`
- `docker network inspect ...` shows a container endpoint id, but
  `docker ps -a` or `docker inspect <id>` can no longer find that container

This has shown up after interrupted real-runtime runs and after VM instability.

### Detection

Check the remaining network directly:

```bash
docker network inspect <network_name> --format '{{json .Containers}}'
```

If the endpoint is listed there but the backing container no longer exists,
Docker is holding stale endpoint metadata.

### Safe Recovery

Only do this when no important non-Skyseed containers are running on the VM.

1. Confirm the residue is limited to stale Skyseed networks/endpoints.
2. Restart Docker:

```bash
sudo systemctl restart docker
until [ "$(systemctl is-active docker)" = active ]; do sleep 1; done
```

3. Re-check the network:

```bash
docker network inspect <network_name> --format '{{json .Containers}}'
```

4. If the endpoint map is now empty, remove the leftover `seed_output`
   network and re-run preflight.

This is still a scoped Skyseed recovery step. Do not use it as a substitute
for broad Docker cleanup.

## Known Issue: 12UT Catalog Must Be Explicit For Full-Shell G/Z Runs

### Symptoms

- A run intended to validate the full `12UT/6GW` ground model starts with
  `--max-uts 12 --max-gateways 6`, but only the default smaller UT catalog is
  selected.
- The runner waits for or watches `ut101`, while compose created only `ut1` to
  `ut4`.
- Bring-up fails with an error like `timed out waiting for containers: ut101`.

### Detection

Before treating the run as representative, inspect:

- `events.jsonl` `preview_started` command includes both explicit catalog
  arguments;
- `preview_summary.ground_model.selected_ut_ids` contains `ut101` through
  `ut112`;
- the compose `ground-endpoints` stage contains
  `hnode_250_ut101` through `hnode_250_ut112`;
- `ground_model.selected_ut_count=12` and
  `ground_model.selected_gateway_count=6`.

### Fix

The historical failure was caused by a command/catalog mismatch: the watcher and
flow IDs named UTs that the command did not select. In the current active path,
do not reconstruct that command. Use the validation profile handoff and make
sure any diagnostic watcher or observer names IDs selected by that same profile.

### Evidence

- Invalid post-reboot short attempt:
  `satellite/output/starlink_shell_experiments/short-1shell-12ut6gw-fresh-postreboot-20260611`
- Corrected catalog attempt:
  `satellite/output/starlink_shell_experiments/short-1shell-12ut6gw-catalog-postreboot-20260611`

## Known Issue: Single-UT Watcher Is Not Enough For 12UT/6GW Gatekeeping

### Symptoms

- Live epoch events show `ping_success=true` for the configured watcher, for
  example `ut101:as154-host-new`.
- The final runtime outcome is still:
  `failure_reason=final_runtime_connectivity_validation_failed`,
  `real_apply_success=false`, and `ping_success=false`.
- `real_runtime_report.json.final_runtime_connectivity_validation` shows one
  or more non-watched UTs failing with `100% packet loss`.

### Detection

Inspect the final validation section, not only `events.jsonl` epoch summaries:

```bash
python3 - <<'PY'
import json, pathlib
run = pathlib.Path("satellite/output/starlink_shell_experiments/<run_id>")
report = json.loads((run / "real_runtime_report.json").read_text())
for attempt in report["final_runtime_connectivity_validation"]["attempts"]:
    bad = [r for r in attempt["flow_results"] if not r.get("success")]
    print("bad flow count:", len(bad))
    for row in bad[:10]:
        print(row["ut_id"], row["target_host_node_id"], row["selected_gateway_id"], row["stdout"].splitlines()[-1])
PY
```

For the 2026-06-11 corrected short gate
`short-1shell-12ut6gw-catalog-postreboot-20260611`, all `25` live epochs
exported final artifact records and the environment returned clean, but final
validation failed for `25/30` flow checks across `ut102` and later UTs.

### Fix

Do not continue to a long run when this happens. Preserve the run directory,
classify it as a final-connectivity gate failure, and inspect the selected UT
to gateway/satellite paths before changing runtime or G/Z semantics.

This is distinct from startup hygiene:

- the corrected run selected `12` UTs, `6` gateways, and `172` satellites;
- `compose_audit.json` had `dynamic_satellite_link=0`;
- post-run preflight was clean with zero scoped containers, networks,
  direct-backend interfaces, and runtime locks.

## Known Issue: Gateway `iptables` Apply Can Hit The Xtables Lock

### Symptoms

- A real runtime reaches the first `RuntimeEpoch`, then fails during
  node-local gateway apply before any epoch artifact is exported.
- The failure context points at a gateway batch such as `gw2:apply batch failed
  with 4`.
- The batch stderr says:
  `Another app is currently holding the xtables lock. Perhaps you want to use the -w option?`

### Detection

Inspect:

- `real_runtime_report.json.failure_context.exception.cause.node_local_batch_failure`;
- `events.jsonl` for `runtime_exception`;
- `run_manifest.json.outcome.failure_reason`.

The 2026-06-11 strict short rerun
`short-1shell-12ut6gw-finalwarmup-20260611` failed this way on `gw2` while
installing gateway filter/NAT state. The run still completed rollback and
post-run preflight was clean.

### Fix

This is an iptables/xtables lock race between node-local gateway operations,
not evidence that prior successful long runs were invalid. Runtime-generated
`iptables` commands should use `iptables -w <seconds>` so concurrent gateway
apply waits for the lock instead of failing immediately with return code `4`.

Do not broaden this into a general rerun policy. Preserve the failed run
directory, confirm post-run preflight is clean, then rerun only after the
iptables wait behavior is present and unit tests cover it.

## Host MPLS Not Ready After Reboot

### Symptoms

- host preflight says MPLS is blocked
- `/proc/sys/net/mpls/platform_labels` is missing or zero
- real runtime fails before or during MPLS programming

### Detection

```bash
/home/seed/miniconda3/envs/seed310/bin/python satellite/apps/skyseed_preflight.py --format text
/home/seed/miniconda3/envs/seed310/bin/python satellite/apps/prepare_host_mpls.py --probe-only --format text
```

### Fix

```bash
/home/seed/miniconda3/envs/seed310/bin/python satellite/apps/prepare_host_mpls.py --format text
```

Re-run preflight after this.

## Known Issue: Interrupted Runtime During Rollback Leaves Host Residue

### Symptoms

- a real runtime is stopped with `Ctrl-C` or another signal
- `docker-compose down` finishes, but host preflight still reports
  `direct_backend_host_interfaces_present`
- the interrupted run directory may contain `docker_compose.json` but be
  missing `run_manifest.json`, `events.jsonl`, or `real_runtime_report.json`

### Detection

Run:

```bash
/home/seed/miniconda3/envs/seed310/bin/python satellite/apps/skyseed_preflight.py --format text
```

Look for:

- `direct_backend_host_interfaces_present`
- non-zero `direct_backend_host_interfaces`

### Fix

1. Use the scoped cleanup suggestions printed by preflight.
2. Remove only matching Skyseed direct-backend interfaces such as
   `sbr*`, `vhs*`, and `vtm*`.
3. Re-run preflight and confirm:
   - `compose_project_containers=0`
   - `compose_project_networks=0`
   - `direct_backend_host_interfaces=0`

### Notes

- This is a host cleanup issue, not evidence that temporal fidelity failed.
- The runner should still be preferred over manual Docker cleanup, because it
  preserves scoped artifacts such as `docker_compose.json` for forensics.
- For long event-time runs, treat interrupted teardown separately from live
  runtime success. If the epoch queue already finished and rollback was the
  only remaining work, record the run as a teardown-interrupted partial result
  rather than as direct evidence that update scheduling failed.

## Real Starlink Experiments

`run_starlink_shell_experiment.py` now records a scoped preflight for
`real-runtime` runs unless `--preflight-policy off` is used. Use the `seed310`
interpreter for real runs so the local `seedemu` dependency is imported with a
supported Python version.

Useful modes:

```bash
/home/seed/miniconda3/envs/seed310/bin/python satellite/apps/run_starlink_shell_experiment.py ... --preflight-policy warn
/home/seed/miniconda3/envs/seed310/bin/python satellite/apps/run_starlink_shell_experiment.py ... --preflight-policy require-clean
```

- `warn`: record blockers and continue
- `require-clean`: stop before runtime if the preflight is not clean

Use `require-clean` for larger or more expensive runs such as `72/172`.

## Known Issue: Compose Down Leaves Orphan Placeholder Helper Containers

### Symptoms

- a fresh real-runtime run fails during the helper startup stage
- compose stderr shows `Recreating ut3-placeholder-rtr` or a similar
  placeholder helper name
- the failure says a running container cannot be removed first
- the machine may still contain containers with names like
  `cba21771f131_ut3-placeholder-rtr`

### Detection

Run:

```bash
/home/seed/miniconda3/envs/seed310/bin/python satellite/apps/skyseed_preflight.py --format text
```

Look for:

- `named_skyseed_containers_present`
- non-zero `named_skyseed_containers`
- helper-like containers ending in `-placeholder-rtr` even when they do not
  carry the `seed_output` compose label

### Fix

1. Use the scoped cleanup suggestions from preflight.
2. Remove only the reported orphan Skyseed helper containers.
3. Re-run preflight and confirm:
   - `compose_project_containers=0`
   - `named_skyseed_containers=0`
   - `compose_project_networks=0`

### Notes

- This is a startup hygiene problem, not a temporal-runtime correctness bug.
- The runner now stops earlier when `compose_down_before` still leaves scoped
  Skyseed residue, instead of continuing into helper startup and failing later.

## Known Issue: 72+ Staged Compose Startup Fan-Out

### Symptoms

- cold `72+` startup hangs or times out in a later satellite batch
- most infrastructure containers are already up, but some `stl-*` services are
  still missing
- a reuse-backed runtime would succeed if the base environment were finished

### Detection

Inspect:

- `run_manifest.json`
- `docker_compose.json`
- `compose_up_stage_results`
- `runtime_blocker.compose_service_snapshot`

Also run:

```bash
python3 satellite/apps/skyseed_preflight.py --format text
```

Look for:

- `docker_compose_up_timeout`
- `seed_output_partial_container_state_present`
- `seed_output_compose_resources_present`

### Supported Recovery

The staged startup path is now a supported baseline for `72+`:

## Known Issue: Disabling Overall Compose Startup Timeout Used To Disable Stage Recovery

### Symptoms

- a real run appears to hang in a later staged startup phase such as
  `mini-internet-*`, `ground-endpoints`, or `satellites-*`
- `runtime_progress.json` continues to heartbeat but `last_progress_utc` stops
  advancing for the current startup stage
- `docker-compose up` child processes stay alive for minutes with no stage
  completion, even though earlier validated runs only advanced because the
  stage timeout triggered retry/recovery

### Root Cause

Older runner behavior treated `--compose-up-timeout-seconds 0` or an exhausted
overall startup deadline as if per-stage startup watchdogs were also disabled.
That could silently remove the retry/recovery path for later staged startup
phases and lead to indefinite waiting.

### Durable Fix

The runner now separates:

- the overall startup deadline
- the per-stage watchdog/recovery timeout

Even when the overall startup deadline is disabled or would otherwise have
been exceeded, per-stage timeout/recovery remains active. For the current
`operational_clean` profile, whole-startup elapsed budgets are disabled and are
not warning or failure evidence; startup wall time is recorded for offline
analysis only.

### Detection

Inspect:

- `runtime_progress.json`
- `docker_compose.json`
- `events.jsonl`

Look for:

- `compose_up_elapsed_budget_observed` only if a nonzero whole-startup elapsed
  budget was explicitly configured for diagnostic observation
- `compose_up_stage_retry_started`
- `compose_up_stage_retry_completed`
- stage results where `overall_startup_deadline_exceeded_before_stage=true`
  but `recovered_after_timeout=true`

### Operator Guidance

- For `operational_clean`, keep `--compose-up-timeout-seconds 0`,
  `--startup-absolute-max-seconds 0`, and `--startup-stall-warning-seconds 0`
  unless introducing a separately justified state/progress-based startup
  contract.
- Prefer the normal staged startup path with fixed per-stage watchdogs and
  recovery.
- If a startup phase still hangs without emitting retry/recovery events, treat
  that as a runner/watchdog regression and inspect artifacts before rerunning.

- infrastructure first
- satellite batches after that
- timeout-time snapshot capture
- missing-service retry after a batch timeout

If a staged batch still blocks:

1. inspect the run artifacts first
2. do not rerun blindly
3. if the compose environment is mostly healthy and fully running, prefer a
   reuse-backed runtime attempt over rebuilding everything immediately

### Fix / Mitigation

- use `--compose-startup-mode auto` or `infra-then-sat-batches`
- keep `--preflight-policy require-clean` for cold runs
- use reuse only when the runner reports
  `cache_digest_match_and_all_required_containers_running`

## Known Issue: VM Reboot Resets Legacy Host MPLS / Bridge Netfilter Readiness

### Symptoms

- after a VM reboot or IDE reconnect, scoped Skyseed residue may be clean but a
  legacy real MPLS runtime is not immediately safe to start
- `skyseed_preflight.py` reports `ready=true` with warnings such as
  `host_mpls_not_ready` and `bridge_netfilter_passthrough_not_ready`
- Docker compose containers/networks and direct-backend interfaces are `0`, but
  MPLS kernel modules are unloaded and `net.mpls.platform_labels` is missing

### Detection

Run from the repository root with the seed310 interpreter and an empty
`PYTHONPATH`:

```bash
env -u PYTHONPATH /home/seed/miniconda3/envs/seed310/bin/python \
  satellite/apps/skyseed_preflight.py --format json
```

Inspect:

- `assessment.warning_reasons`
- `assessment.ready`
- `checks.host_mpls.assessment`
- `checks.bridge_netfilter`
- `compose_residue_summary`

The `gz-long-1shell-2h-real-contextprune-20260610-1854` post-run preflight was
clean inside the run. A later reboot check remained residue-clean but showed
`host_mpls_not_ready` and `bridge_netfilter_passthrough_not_ready`, which is a
host preparation issue rather than a G/Z runtime cleanup failure.

Interpretation rule:

- residue fields answer whether a previous run left scoped compose or
  direct-backend artifacts behind
- host readiness fields answer whether the VM is prepared for a future legacy
  real MPLS run
- `ready=true` with warning reasons is acceptable evidence of a clean machine
  after a run, but it is not permission to start the next legacy real MPLS run
  without host preparation
- for the current BPF-owned `operational_clean` iteration, use
  `skyseed_session_check.py` and its `operational_iteration_ready` field; legacy
  host MPLS/bridge warnings are intentionally suppressed in that operational
  display so they do not distract from BPF contract readiness

### Supported Recovery

- do not start another legacy real MPLS runtime while these warnings are present
- rerun the existing host preparation helper before the next real run
- rerun preflight afterward and require MPLS/bridge readiness to be green
- keep `dynamic_satellite_link=0` and use the same SEED Mini Internet setup
  when repeating G/Z long-run validation

## Known Issue: Long Full-Shell Cleanup And Artifact Growth

### Symptoms

- long full-shell event-time runs finish connectivity and temporal validation
  but spend substantial wall time in final cleanup/rollback
- run artifacts grow linearly with RuntimeEpoch / ParameterEpoch count
- memory pressure can become visible on small VMs, especially after the earlier
  pre-prune OOM failure

### Detection

Inspect:

- `real_runtime_report.json`
- `timing.json`
- `runtime_progress.json`
- `epoch_execution_details.jsonl`
- the long-run metrics artifact under `output/gz_scale_12ut6gw_*`

For the compact analysis view, generate:

```bash
env -u PYTHONPATH /home/seed/miniconda3/envs/seed310/bin/python \
  satellite/apps/summarize_starlink_long_run.py \
  satellite/output/starlink_shell_experiments/<run-id> \
  --gz-semantics satellite/output/gz_scale_12ut6gw_*/<gz-artifact>.json \
  --current-preflight /tmp/<current-preflight>.json \
  --output satellite/output/gz_scale_12ut6gw_*/<baseline-summary>.json
```

The summary explicitly separates:

- RuntimeEpoch versus ParameterEpoch timing
- activation lag versus transition wall time
- strict deadline misses versus ping loss
- run-local post-run preflight versus current host readiness
- RSS/resource observations versus durable artifact/log growth

For the completed `2h` G/Z run, final cleanup succeeded and post-run preflight
was clean, but the run still produced about `335 MiB` of artifacts and reported
direct-backend cleanup of `193` bridges and `1473` host interfaces. Earlier
sampling also showed high swap pressure near the end of the run.

### Fix / Mitigation

- keep the compact report/detail JSONL retention model; do not restore embedded
  full epoch details in the manifest
- preserve current G/Z shared-return semantics; this is a runtime resource and
  cleanup-cost issue, not a semantic one
- next optimization should target node-local/final cleanup batching, report
  retention, and host resource headroom before attempting longer unattended
  runs

## Known Issue: Post-Reboot 6-Zone Smoke Timing Buckets

### Symptoms

- a short 6-zone metadata real-runtime smoke can look slow if cold prewarm,
  first materialization, steady RuntimeEpoch, and final rollback/cleanup are
  combined into one timing number
- VM reboot can leave the workspace clean but require a fresh preflight and host
  MPLS/sysctl readiness check before any real MPLS runtime
- the legacy `inspect_gz_return_semantics.py` dry-run check can report a G/Z
  failure on the current command-state schema because it compares conceptual FEC
  groups against route plus LFIB command items

### Detection

Use the run-local artifacts first:

- `satellite/output/starlink_shell_experiments/gz-z6metadata-real-smoke-postreboot-20260611-0758/preflight.json`
- `satellite/output/starlink_shell_experiments/gz-z6metadata-real-smoke-postreboot-20260611-0758/post_run_preflight.json`
- `satellite/output/analysis/real_smoke_6zone_20260611_0750_postreboot/final_smoke_metrics.json`

For the completed post-reboot 6-zone smoke:

- steady RuntimeEpoch count excluding initial materialization was `25`
- steady RuntimeEpoch transition wall was avg `6.557622s`, p95 `15.78472s`,
  max `21.553504s`
- first RuntimeEpoch materialization was `61.205623s` and is not part of steady
  RuntimeEpoch timing
- cold prewarm before first epoch execution was `1185.126851s`; host runtime
  prewarm was `998.417482s`
- final teardown/cleanup wall was `498.632062s`, including node-local rollback
  `213.229749s`, host rollback `191.637771s`, and host artifact cleanup
  `90.561722s`
- post-run preflight was clean: scoped compose containers/networks,
  direct-backend interfaces, and run locks were all cleared

G/Z evidence should come from `return_path_audit.json` and the temporal
inspector output. For this smoke they reported `shared_gz_backbone`,
`current_return_state_scales_with_flow_count=false`, and
`ut_mpls_egress_count=0`. Treat the older dry-run semantics inspector as
non-authoritative until it is updated for the current command-state accounting.

### Fix / Mitigation

- before every post-reboot real run, record preflight and host preparation
  artifacts, then require a clean post-run preflight after rollback
- report steady RuntimeEpoch timing excluding the initial materialization row
- keep cold prewarm, first materialization, ParameterEpoch, steady RuntimeEpoch,
  and final teardown/cleanup as separate timing buckets
- do not treat rollback/artifact cleanup as data-plane RuntimeEpoch cost
- preserve current G/Z semantics; the remaining risk is runtime overhead and
  cleanup batching, not a planner semantic regression

## Long-Run Discipline: Operational 3H Iteration Rounds

### Symptoms

- a later expensive run can appear to regress to an older class of failure when
  the real issue is a stricter guard exposing an adjacent contract gap earlier
  in the run
- repeated failures can be misclassified if each run is summarized in isolation
  instead of being compared against prior round artifacts
- cheap local checks can become counterproductive if they encode a one-run
  special case or block valid operational-clean paths

### Detection

For every failed operational 3H iteration, compare the new failure against the
nearest prior round artifacts before editing:

- runtime progress: horizon/slice reached, runtime epoch count, and first
  fail-closed stage
- maintenance artifacts: ready/blocked/failed status, preload record counts,
  forbidden-work audit, and cleanup dependency reports
- BPF generation evidence: slot assignments, runtime plan proof generation,
  preloaded generation records, and active flip/consume records
- forbidden work counters: fallback, full reload, full scan, live full map
  dump/diff, runtime attach, and premature active/future-state exposure

If the same class appears again, treat it as a shared contract or module-level
problem until proven otherwise. Do not spend the next long run only checking
whether the previous error string disappeared.

### Fix / Mitigation

- prefer module/contract fixes over H3-specific branches or run-id-specific
  behavior
- add cheap pre-run checks only when they assert stable contracts and do not
  require live scans or hidden reconciliation
- after each fix, inspect adjacent consumers: runtime readiness, worker
  handoff, compact artifacts, reports, cleanup, and failure propagation
- keep the goal of each long run as completing the 3H operational iteration;
  bug-specific validation is only one checkpoint on that path

## Operational-Clean Worker Budget and Edge Route State

### Symptom

- a process-owned horizon maintenance worker fails before boundary with
  `bpf_resident_attach_coverage_budget_exceeded`
- the failure artifact shows a nonempty attach plan and a default
  `max_attach_interfaces` budget even though the process work input already
  carries a larger dynamic `resident_attach_coverage_budget` / `bpf_attach_budget`
- steady runtime epoch timing regresses while qdisc remains small; node-local
  apply shows repeated UT default-route or GW return-route replacements

### Detection

- Run the offline horizon-maintenance process audit on newly prepared artifacts
  before any expensive run. A process compact manifest with queued preload work
  must include resident attach budget evidence, and the terminal ready/failure
  report must show that attach prepare consumed that budget instead of the
  default.
- Compare steady runtime node-local apply against the last known clean
  baseline. Repeated `route_replace` batches in clean BPF runtime are evidence
  of lost session state, not qdisc regression.

### Required Invariants

- Capacity limits for BPF resident attach and generation windows must be derived
  from the prepared horizon/window artifacts. Do not use a fixed default as the
  operational-clean limit when a prepared run has dynamic budget evidence.
- The rolling resident attach budget from the precompose role-union gate must be
  carried into process worker payloads, frozen compiled inputs, frozen-manifest
  proofs, and unioned attach inputs.
- A process worker must not fall back to the default attach budget when the
  prepared run has a rolling budget.
- The runtime main loop must not live-compile future generation BPF maps from
  context before the future physical interfaces exist. The only target-
  generation path is process-owned maintenance: additive host preparation,
  exact targeted final binding capture, strict freeze/compile from the frozen
  context, attach capability preparation, then inactive generation preload.
  Precompiled target inputs are rejected; there is no compile-allowed flag or
  alternate source. The worker must carry the dynamic resident attach budget
  and fail closed without default budgets, full reloads, full scans, or hidden
  reconciliation.
- Process-owned maintenance workers must take physical file paths from the
  process work payload only. Work items may carry snapshot/runtime-plan
  semantics, but they must not choose their own topology/link-state file path,
  and missing payload paths must fail in offline worker replay before compose.
- BPF-owned edge route preflight should avoid live probing, but successful
  route installation must be reflected in the active session preflight. Do not
  represent "not probed live" as "known missing" on every later epoch.
- Clean runtime must not regenerate UT/GW edge route replacements every epoch
  unless the active session state says that specific edge route is missing.

## Operational-Clean Node-Local Boundary

### Symptom

- `node_local_apply` appears in clean BPF-owned runtime and can be mistaken for
  satellite-core Linux route installation.
- This is a correctness issue, not only a timing issue, if any retained
  node-local command installs per-UT Linux routing/MPLS state in a satellite
  namespace.

### Required Invariants

- In `operational_clean` / `bpf_owned`, satellite-core G/Z forwarding is owned
  by BPF maps and resident BPF attachment only.
- Retained Linux node-local commands in clean BPF runtime are limited to edge
  endpoint bootstrap, currently UT default route and GW local-origin BPF return
  route.
- Serving-satellite per-UT delivery routes, policy rules, MPLS LFIB, shared
  zone-return Linux routes, NAT/FORWARD fallback, full reload, full scan, and
  hidden reconcile remain forbidden in the clean runtime path.
- Timing reports should classify allowed edge endpoint routes separately from
  generic `ip route` work so a larger `node_local_apply` cannot hide semantic
  pollution.

## Prepared Bundle Runtime Input Size

### Symptom

- Startup/freeze becomes comparable to compose time.
- `executable_runtime_inputs.json` grows to GiB scale because it embeds full
  normal runtime inputs rather than referencing the canonical prepared runtime
  input artifact.

### Required Invariants

- `normal_runtime_inputs` is the canonical thaw source and should be stored as
  its own prepared artifact with a stable hash.
- `executable_runtime_inputs` should carry execution plans, small indices, and a
  reference/hash to normal runtime inputs, not another embedded copy of the full
  payload.
- `normal_runtime_inputs` should not persist indexes that are exactly derivable
  from the scoped canonical context, such as per-segment context, runtime plan,
  distribution batch, topology snapshot, or top-level current-horizon context
  copies. Build these indexes in memory for consumers that need them.
- Loading code may hydrate the reference for existing consumers, but repeated
  parsing/hash of the same large artifact in one process should be cached.
- Startup optimization should remove duplicated whole-run materialization before
  adding more timing probes.
- Minimal phase progress is useful when it names an existing expensive
  materialization phase, but it must not become a new live runtime guard or a
  report path that blocks epoch updates.

## Frozen PID Seal, Repeated Binding Work, and Artifact Amplification

### Symptom

- a ready maintenance worker is rejected at the H boundary with
  `horizon_maintenance_handoff_pid_cache_mismatch` even though every PID value
  matches
- H0 inactive generation preload spends hundreds of seconds in interface
  discovery or clsact checks while map writes take only a few seconds
- worker/ready/handoff artifacts grow by embedding the same compiled maps or
  preload reports for every generation
- final cleanup spends minutes deleting container peers and temporary names
  that were already removed by host-veth deletion or veth rename

### Required Invariants

- The initial targeted binding producer must seal the exact populated PID
  cache before any maintenance request is emitted. Worker requests consume only
  the sealed snapshot. An unsealed cache and PID value drift have distinct
  fail-closed errors.
- H0 and maintenance preload consume frozen compiled inputs plus prepared
  attach authority. Interface discovery and live clsact guards are zero on the
  preload/runtime path.
- Worker binding requirements are derived directly from frozen contexts. Do
  not run an offline full compile merely to discover refs and then strict-
  compile the same generation again.
- Final binding capture remains an exact ref operation. Batch requested
  interfaces per frozen node/PID and use bounded node parallelism; never replace
  it with a namespace link scan.
- Maintenance delta attach may use bounded parallelism across distinct
  interfaces after physical preparation. It must retain delta-only attach,
  frozen control-plane addresses, and zero full attach/reload.
- Ready/handoff generation records contain activation identity and compact
  proof only. Full compiled maps and nested preload reports remain in their
  owning frozen/worker artifacts and are not copied into every consumer.
- Cleanup deletes owned host veths and bridges only. Host-veth deletion owns
  peer removal; completed temporary names and paired container endpoints are
  counted as elided work, not executed no-ops.

## When To Update This File

Add a new entry when all are true:

- the failure has happened more than once
- the failure is not obvious from the normal CLI output
- there is a reliable detection method
- there is a scoped fix or mitigation
