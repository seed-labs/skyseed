# Skyseed Event Timeline Update Planner

Date: 2026-06-21

Scope: event-timeline planning foundation, runtime queue wiring, preview
counters, docs, focused tests, and one short bounded real-runtime verification.
This pass does not change G/Z semantics, FEC semantics, MPLS-like forwarding
intent, qdisc implementation, TheaterQ, kernel modules, or BPF live preload
behavior. It did not run S4/300+ or a long S5 A/B. Runtime artifacts were
written under `/tmp`; the runner's transient active lock under
`satellite/output` was removed during cleanup and no `satellite/output`
artifact is part of this result.

## Current Behavior

Skyseed already separates dense physical evaluation from executor epochs more
than the old terminology implied. Physical orbit/topology samples are produced
at the configured scan interval, then `temporal_planning.py` emits
`RuntimeEpoch`s only when semantic runtime diffs exist and `ParameterEpoch`s
only when parameter thresholds or active/drop state transitions require a
refresh.

The compatibility executor names remain:

- `RuntimeEpoch` is the current executor-compatible adapter for a
  `RuntimeTransaction`.
- `ParameterEpoch` is the current executor-compatible adapter for a
  `ParameterUpdateBatch`.

Real runtime queue construction now consumes `TemporalEvent` records when the
temporal plan has an `event_queue`. The queue entries keep the executor's
existing `epoch_type` field, but also carry `source=event_timeline` and
`event_timeline_type=runtime_transaction` or `parameter_update_batch`.

## New Event-Timeline Model

The planning surface now reports the model explicitly:

- `PhysicalSample`: orbit, coverage, link, and parameter observation at a
  sampled time.
- `CandidateEvent`: a possible semantic or parameter change detected between
  adjacent samples.
- `RefinedEvent`: bounded local event-time estimate inside the adjacent
  physical-sample interval when the transition type is safe to refine.
- `LinkSpan`: sampled interval of equivalent active link topology.
- `AttachmentSpan`: sampled interval of equivalent UT/zone/GW attachment.
- `FECPathSpan`: sampled interval of equivalent G/Z FEC path content.
- `RuntimeTransaction`: coalesced semantic forwarding/topology/FEC update.
- `ParameterUpdateBatch`: coalesced qdisc/BPF-parameter update batch.

Event refinement is implemented for coverage invalidation, UT serving
satellite handover, zone active serving satellite / anchor change, GW
selection change, and structural link active/inactive changes. The current
planner does not run a full analytical continuous solver; it brackets the
event between adjacent physical samples and emits a precision-bounded local
bisection estimate. Unsupported runtime reasons and all current qdisc
ParameterUpdateBatch events fall back to the sample boundary and record an
explicit refinement reason.

## Implemented Now

`TemporalPlan.event_time_summary.event_timeline_projection` now records:

- physical samples discover candidate events only;
- sample-aligned vs refined event counts;
- compatibility adapters to `RuntimeEpoch` and `ParameterEpoch`;
- runtime transaction and parameter update batch counts;
- LinkSpan, AttachmentSpan, FECPathSpan, and ParameterTimeline point counts.

`preview_update_counters` now uses
`skyseed-event-timeline-preview-counters-v1` and includes the old epoch fields
plus event-timeline names:

- `physical_sample_count`, `candidate_event_count`, `refined_event_count`,
  `sample_aligned_event_count`, `skipped_no_op_sample_count`;
- `refinement_supported_event_count`, `refinement_failed_event_count`,
  `refinement_unsupported_event_count`, `refinement_precision_seconds`, and
  `refinement_reason_breakdown`;
- `runtime_transaction_count`, `parameter_update_batch_count`,
  `coalesced_event_count`;
- span counts and parameter timeline point count;
- UT serving, zone anchor, GW selection, G/Z FEC, G->Z, Z->G, and structural
  link active/inactive counts;
- delay, bandwidth/rate, loss/drop, and active/inactive parameter counts;
- BPF generation count, active generation switch count, expected BPF-owned
  scope count, expected Linux MPLS/LFIB elision count, expected qdisc update
  count, and expected lifecycle/link operation count;
- ignored cost-only and metadata-only changes;
- dwell/hysteresis/coverage-invalidation stability counters.

Coverage invalidation is now a runtime semantic trigger. Required active zones
that move from resolved to unresolved force a transaction even if stability
policy would otherwise prefer to hold state.

`run_starlink_shell_experiment.py` now writes event-timeline fields into:

- `epoch_execution_queue.json`;
- `prepared_update_bundle.json`;
- `run_manifest.json.temporal_summary`;
- `real_runtime_report.json.prepared_bundle_runtime`;
- `temporal_audit.json`;
- compact and detail epoch execution records.

## Preview-Only

BPF planning metadata is preview-only here. The reported support status remains
truthful:

- current generation: supported;
- next-generation preload: not implemented;
- multi-generation horizon preload: not implemented;
- multi-generation horizon preload design: ready for a later backend pass.

qdisc-owned delay/rate/bandwidth remain discrete updates. No function-driven
kernel/qdisc behavior is implemented.

## Preview Evidence

Canonical short S5 preview:

- Command shape: planned-shell `S5`, `172` satellites, `10s` sample scan,
  `120s` horizon, `--skip-seed-render`, `/tmp` artifacts only.
- Physical samples: `13`.
- Old fixed-sample runtime upper bound: `13`.
- RuntimeTransaction output: `3`.
- Old fixed-sample parameter upper bound: `13`.
- ParameterUpdateBatch output: `10`.
- Candidate events: `12`.
- LinkSpan / AttachmentSpan / FECPathSpan: `188 / 4 / 8`.
- BPF generations / expected BPF scopes / Linux MPLS LFIB elision:
  `3 / 12 / 12`.
- Expected qdisc updates: `64`.
- Refined / sample-aligned events: `2 / 11`.
- Refined event examples: runtime transaction for
  `starlink-shell-slice-0007` detected at physical sample `60s`, scheduled at
  refined event time `55s`; runtime transaction for
  `starlink-shell-slice-0008` detected at physical sample `70s`, scheduled at
  refined event time `65s`. Both used `bounded_local_bisection` and a `1s`
  refinement precision.

Short S4 preview:

- Command shape: planned-shell `S4`, `348` satellites, `10s` sample scan,
  `60s` horizon, `--skip-seed-render`, `/tmp` artifacts only.
- Physical samples: `7`.
- RuntimeTransaction output: `2`.
- ParameterUpdateBatch output: `5`.
- BPF generations / expected BPF scopes / Linux MPLS LFIB elision:
  `2 / 8 / 8`.
- Expected qdisc updates: `36`.

This S4 evidence proves the counters run on a 348-satellite preview, but it is
not a full S4 readiness PASS. Broader UT/GW/zone catalog coverage and a longer
preview window remain pending before declaring S4 ready.

## Bounded Runtime Evidence

Short strict bounded real-runtime verification:

- Command shape: planned-shell `S5`, `24` satellites, `10s` scan, `120s`
  horizon, `--max-runtime-epochs 4`, `--max-parameter-epochs 1`,
  `--forwarding-backend bpf_owned`, `/tmp` run root.
- Run id: `event-timeline-refined-bpf-owned-strict-netns-attach-v4`.
- Real runtime executed: `true`.
- Queue source: `event_timeline`.
- Physical samples: `13`.
- Selected RuntimeTransaction count: `4`.
- Executed RuntimeTransaction count: `4`.
- Selected ParameterUpdateBatch count: `1`.
- Executed ParameterUpdateBatch count: `1`.
- Skipped no-op sample count: `0`.
- Sample-aligned event count: `9`.
- Refined event count: `4`.
- Refined RuntimeTransaction evidence includes `starlink-shell-slice-0008`.
- BPF-owned backend consumed event-timeline runtime transaction: `true`.
- Strict BPF-owned contract was enforced for eligible G/Z/MPLS-like scope:
  `linux_gz_fallback_enabled=false`, `linux_gz_fallback_command_count=0`,
  `eligible_core_fallback_to_linux=0`.
- The bounded gate skipped eligible Linux core forwarding/source-policy/MPLS
  checks for BPF ownership; no Linux G/Z/MPLS/LFIB fallback was installed.
- qdisc updates executed only from ParameterUpdateBatch: `true`.
- Parameter batch topology/forwarding mutation free: `true`.
- Runtime transaction had expected mutation scope: `true`.
- Strict BPF packet-path validation passed: BPF observed the actual ping path,
  redirected the G/Z path, and exercised return handoff without Linux fallback.
- Strict BPF counter fields:
  `bpf_classifier_miss_count=0`, `bpf_ping_path_hit_count=72`,
  `bpf_return_handoff_hit_count=24`.
- Final connectivity validation phase: pre-rollback.
- Final connectivity validation scope: `reapplied_gateway_class_sample`.
- Final connectivity validation result: `true`.
- Rollback completed: `true`.
- Post-run preflight clean: `true`.
- `event_timeline_execution_success`: `true`.
- `pre_rollback_connectivity_success`: `true`.
- `rollback_success`: `true`.
- `post_run_preflight_clean`: `true`.
- `aggregate_runtime_succeeded`: `true`.

The final connectivity validation is explicitly pre-rollback. Its result is
therefore counted in `pre_rollback_connectivity_success` only when that phase
is reached. Post-run preflight is tracked separately as
`post_run_preflight_clean`. The strict gate passed with BPF-owned netns attach
coverage; earlier host-peer attach and self-redirect issues are fixed without
reintroducing Linux G/Z/MPLS fallback.

## Future Work

- Replace the current endpoint-local bisection oracle with event-specific
  continuous topology/coverage predicates when that oracle is available.
- Add next-generation BPF preload only when maps are actually prepared before
  event time.
- Add multi-generation horizon preload after live backend support exists.
- Add optional piecewise parameter preview, clearly separated from qdisc-owned
  discrete updates.
- Broaden S4 preview evidence with the realistic UT/GW/zone catalogs.

## Tests

Focused deterministic coverage lives in
`satellite/test_sampling_update_optimization.py` and
`satellite/test_event_timeline_runtime_queue.py`. It covers no-op sample ticks,
metadata-only ignores, cost-only ignores, coverage invalidation, structural
active/inactive transitions, parameter thresholds, below-threshold drift,
minimum dwell, coalescing, BPF generation counts, counter consistency, runtime
queue source metadata, continuous-operation summary fields, and compact
execution record preservation.
