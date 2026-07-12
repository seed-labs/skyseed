# Skyseed Sampling Update Model Optimization

Date: 2026-06-21

Scope: implementation plus tests for BPF-aware, event-driven update previews.
This goal does not run S4/300+ real runtime, does not change G/Z semantics, does
not implement qdisc/TheaterQ, does not commit `satellite/output`, and does not
push.

## Implemented Now

RuntimeEpoch emission now uses semantic forwarding diffs instead of
Linux/MPLS-realization churn.

The temporal planner compares:

- semantic G and GZ FEC groups;
- G->Z path and forward link IDs;
- Z->G return path, return target, and return prefixes;
- UT serving satellite and UT access link;
- zone active serving satellite;
- selected default/secondary/exception gateway class and gateway access;
- structural active-link topology.

MPLS label-only changes no longer create a RuntimeEpoch by themselves. Labels
remain in canonical G/Z artifacts and Linux realization, but event emission is
now keyed to semantic forwarding content.

ParameterEpoch emission remains threshold based and now explicitly records
active/drop state transitions:

- `delay_threshold_exceeded`;
- `bandwidth_threshold_exceeded`;
- `loss_threshold_exceeded`;
- `drop_state_changed`;
- `active_state_changed`;
- `routing_cost_changed` remains metadata-driven compatibility.

qdisc-owned fields remain separate from BPF parameter possibilities. Delay,
rate/bandwidth, and qdisc loss are still Linux/qdisc-owned. BPF readiness is
reported as current-generation only unless a future backend really implements
preload.

## Stability Policy

Existing zone anchor stability is preserved:

- coverage margin default: `2.0` degrees;
- handover hysteresis score default: `12.0`;
- gateway hop penalty default: `3.0`.

New configurable policy:

- minimum anchor dwell default: `0.0` seconds, preserving existing behavior.

When configured above zero, `ZoneAnchorQualityGenerator` keeps the current zone
anchor until the dwell expires, unless the previous anchor is near coverage
loss. The selected snapshot itself remains stable, so RuntimeEpoch suppression
does not create a mismatch between snapshot intent and runtime state.

## Preview Counters

Every `TemporalPlan` now exposes `preview_update_counters` and also carries the
same payload inside `event_time_summary.preview_update_counters`.

The counter schema is `skyseed-update-preview-counters-v1` and reports:

- sample count;
- RuntimeEpoch count;
- ParameterEpoch count;
- skipped no-op sample count;
- G/Z FEC path change count;
- G->Z path change count;
- Z->G path change count;
- UT serving satellite change count;
- zone active satellite change count;
- GW selection change count;
- structural link change count;
- qdisc parameter threshold crossing count;
- BPF generation count;
- active generation switch count;
- expected BPF-owned scope count;
- expected Linux MPLS/LFIB elision count;
- expected qdisc command count;
- expected lifecycle/link operation count;
- per-zone update distribution;
- per-UT update distribution.

Estimate basis:

- BPF-owned scope is counted as semantic G/GZ FEC groups per RuntimeEpoch.
- Linux MPLS/LFIB elision is estimated as one elidable Linux/MPLS scope per
  BPF-owned semantic FEC group.
- qdisc commands are estimated as one command per changed ParameterEpoch link.
- lifecycle/link operations are RuntimeEpoch `NetworkMutationPlan` operations
  excluding `update_slot_parameters`.

## BPF Generation Readiness

Preview counters report:

- `supported_mode=current_generation_only`;
- current generation supported: true;
- next-generation preload supported: false;
- multi-generation horizon preload supported: false;
- multi-generation horizon preload design-ready: true.

This is intentionally conservative. The implementation does not pretend that
next-generation or multi-generation preload exists in live runtime.

## S4 Preview Readiness

`build_starlink_shell_preview.py` now includes
`temporal_plan.s4_preview_readiness` in dry-run preview summaries. It reports:

- whether the preview is an S4 planned-shell preview;
- expected RuntimeEpoch and ParameterEpoch density;
- expected G/Z forward and return FEC counts;
- expected BPF-owned scope;
- UT/GW/zone coverage summary;
- boundary UT count;
- remote-return UT count;
- risky coverage-margin zones;
- recommendation to proceed with bounded S4 runtime or review/consider S3/720
  fallback.

This is preview support only. It does not run real runtime.

## Actual 10s Preview Result

No S4/300+ real runtime was run, no long S5 A/B was run, and no
`satellite/output` artifact was produced for this check.

Important correction: the earlier `/tmp/skyseed_s5_sampling_preview_opt_full`
run used `--sample-interval 300`, `--event-evaluation-interval 300`, and
`--step-seconds 300`. That was a coarse preview workload shape taken from the
realistic workload summary, not the canonical 10s sampling baseline and not
evidence of the sampling/update optimization. The optimization is not allowed
to win by widening the sample interval.

The 300s interval source was:

- explicit CLI arguments in the temporary preview command;
- the coarse `realistic_preview_workload_summary.json` sampling profile
  (`step_seconds=300`, `slice_count=25`, `horizon_seconds=7200`).

It was not:

- a new temporal-planning default;
- a new preview-builder default;
- an S5-specific profile default;
- max-sample/downsampling logic;
- newly introduced code.

The preview builder still defaults `--sample-interval` to unset and
`--step-seconds` to `120.0`; effective sampling uses `sample_interval` when
provided, otherwise `step_seconds`. The real runtime helper also resolves
sample interval from explicit CLI, temporal plan policy, or `step_seconds`.

The historical concrete 10s preview used retired preview catalogs. Do not
replay that command. Current sampling checks should be derived from the active
`rich_s4_operational_clean_bpf` profile and the visualization orbit/gateway
inputs.

This is not an S4 dry run. The `s4_preview_readiness` schema was populated for
an S5 preview with `planned_shell_id=S5` and `is_s4_preview=false`.

Post-optimization counters for that 10s S5 preview:

| Metric | Fixed-sample upper bound | Optimized preview |
| --- | ---: | ---: |
| sample_count | 13 | 13 |
| RuntimeEpoch count | 13 | 11 |
| ParameterEpoch count | 13 | 2 |
| skipped no-op sample count | 0 | 0 |
| RuntimeEpoch reduction ratio | n/a | 15.3846% |
| ParameterEpoch reduction ratio | n/a | 84.6154% |

The reduction ratios above compare against a strict fixed-sample upper bound at
the same 10s sample interval. They are not a long S5 A/B result.

RuntimeEpoch trigger breakdown:

| Trigger | Count |
| --- | ---: |
| initial_runtime_epoch | 1 |
| G/Z FEC path change | 8 |
| G->Z path change | 8 |
| Z->G path change | 8 |
| UT serving satellite change | 4 |
| zone active satellite change | 8 |
| GW selection change | 8 |
| structural link change | 9 |
| BPF generation content change | 8 |
| metadata-only / timestamp-only / cost-only change | 0 |

ParameterEpoch trigger breakdown:

| Trigger | Count |
| --- | ---: |
| bandwidth_threshold_exceeded | 2 |
| routing_cost_changed | 2 |
| delay_threshold_exceeded | 0 |
| loss_threshold_exceeded | 0 |
| active_state_changed | 0 |
| drop_state_changed | 0 |

Preview counter estimates:

| Metric | Value |
| --- | ---: |
| expected BPF generation count | 11 |
| expected BPF-owned scope count | 186 |
| expected qdisc update count | 47 |
| expected lifecycle/link operation count | 236 |

Per-RuntimeEpoch trigger detail:

| Slice | t(s) | Triggers |
| --- | ---: | --- |
| starlink-shell-slice-0001 | 0 | initial_runtime_epoch |
| starlink-shell-slice-0002 | 10 | G/Z FEC, BPF generation, G->Z, Z->G, GW selection, structural link |
| starlink-shell-slice-0005 | 40 | G/Z FEC, BPF generation, G->Z, Z->G, zone active satellite, GW selection, structural link |
| starlink-shell-slice-0006 | 50 | structural link |
| starlink-shell-slice-0007 | 60 | G/Z FEC, BPF generation, G->Z, Z->G, UT serving satellite, zone active satellite, GW selection, structural link |
| starlink-shell-slice-0008 | 70 | zone active satellite |
| starlink-shell-slice-0009 | 80 | G/Z FEC, BPF generation, G->Z, Z->G, UT serving satellite, zone active satellite, GW selection, structural link |
| starlink-shell-slice-0010 | 90 | G/Z FEC, BPF generation, G->Z, Z->G, UT serving satellite, zone active satellite, GW selection, structural link |
| starlink-shell-slice-0011 | 100 | G/Z FEC, BPF generation, G->Z, Z->G, zone active satellite, GW selection, structural link |
| starlink-shell-slice-0012 | 110 | G/Z FEC, BPF generation, G->Z, Z->G, UT serving satellite, zone active satellite, GW selection, structural link |
| starlink-shell-slice-0013 | 120 | G/Z FEC, BPF generation, G->Z, Z->G, zone active satellite, GW selection, structural link |

RuntimeEpoch did not equal sample count at 10s in this preview. The planner
emitted 11 RuntimeEpochs and 2 ParameterEpochs over 13 samples. There were no
metadata-only, timestamp-only, or cost-only RuntimeEpoch triggers in the
preview counters. The high RuntimeEpoch density appears to come from real
semantic changes in G/Z paths, zone anchors, gateway selection, and structural
active-link membership over this S5/full-catalog 120s window, not from the 300s
sampling mistake.

10s S5 dry-run readiness summary:

| Metric | Value |
| --- | ---: |
| satellite count | 172 |
| UT count | 32 |
| GW count | 12 |
| zone count | 16 |
| active zone count | 10 |
| expected RuntimeEpoch count | 11 |
| expected ParameterEpoch count | 2 |
| expected G/Z FEC count | 186 |
| expected BPF generation count | 11 |
| expected BPF-owned scope count | 186 |
| expected qdisc update count | 47 |
| expected lifecycle/link operation count | 236 |

An actual S4 dry-run readiness summary was intentionally not produced in this
pass. The static planned S4 profile remains `348` satellites, but expected
epoch counts, FEC counts, BPF scope, qdisc count, lifecycle/link count, and
risk zones require running S4 preview and must not be inferred from S5.

## Safety Rules

RuntimeEpoch is always emitted for:

- initial runtime materialization;
- semantic G/GZ FEC path changes;
- BPF generation content changes;
- G->Z path changes;
- Z->G return path changes;
- UT serving satellite changes;
- zone active serving satellite changes;
- gateway/default/secondary/exception selection changes;
- real structural active-link up/down changes.

ParameterEpoch is always emitted for:

- active/inactive transition;
- drop-state transition;
- delay threshold crossing;
- bandwidth/rate threshold crossing;
- loss threshold crossing;
- routing-cost compatibility threshold crossing.

Parameter-only delay, bandwidth/rate, loss, and routing-cost drift can be
skipped when it remains inside configured thresholds. Active/inactive
transitions cannot be skipped by thresholding. Drop-state transitions are also
state transitions and should not be hidden as small loss drift.

Minimum dwell can hold a current zone anchor only while that current anchor is
still serviceable. Coverage invalidation breaks dwell: if the previous anchor
falls near coverage loss, the handover is allowed even if the dwell timer has
not expired.

## BPF Update Support Boundary

Implemented today:

- current generation support;
- active generation switch per emitted RuntimeEpoch;
- preview counters for expected BPF-owned scope and Linux MPLS/LFIB elision.

Design-ready but not implemented:

- next-generation preload;
- multi-generation horizon preload.

Not implemented as live runtime:

- timed future `active_generation` switches independent of emitted
  RuntimeEpoch application;
- qdisc/TheaterQ replacement.

This optimization did not change G/Z, FEC, or MPLS-like semantics. It changed
epoch emission rules, preview counters, thresholding/accounting, and the
optional zone-anchor dwell policy.

## Future Work

- Make next-generation preload live.
- Add multi-generation horizon map preloading and generation garbage collection.
- Promote routing cost, delay bucket, bandwidth floor, loss/drop, coverage
  margin, and dwell penalty into a full route ranking model.
- Refine Linux MPLS/LFIB elision estimates against measured BPF-owned command
  elision once broader bounded previews are available.
- Keep qdisc optimization separate; TheaterQ/custom qdisc work remains out of
  scope.

## Validation

Focused deterministic tests:

```bash
python -m unittest satellite.test_sampling_update_optimization
```

Coverage:

- label-only MPLS churn does not emit a RuntimeEpoch;
- semantic path/FEC change emits a RuntimeEpoch;
- ParameterEpoch requires threshold crossing;
- preview counters report update volume;
- minimum dwell keeps the current zone anchor until the dwell expires.
