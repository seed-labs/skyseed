# Skyseed Sampling And Update Model Audit

Date: 2026-06-21

Scope: audit and design only. This pass did not implement sampling changes, did
not change G/Z semantics, did not add a new scheduler, did not run S4 or any
300+ satellite workload, did not write or commit `satellite/output`, and did not
push.

## Executive Summary

Skyseed is currently mixed rather than purely fixed-interval or purely
event-driven.

The orbit/visibility layer evaluates dense samples at a configured cadence. The
temporal planner then compresses those samples into RuntimeEpochs,
ParameterEpochs, ObservationFrames, and no-op samples. RuntimeEpochs are
event-driven by forwarding/topology/FEC-relevant diffs. ParameterEpochs are
threshold-driven, but can also be bounded by `parameter_max_interval` when
parameter drift exists. ObservationFrames are sampled separately for reporting.
Rolling horizon replenishment is live in continuous runtime, but it creates new
prepared segments rather than changing the semantic epoch model.

The commonly used 10s cadence is not a semantic truth in code. It appears in
run commands and baselines as workload input. The code accepts arbitrary
positive `--sample-interval`, `--event-evaluation-interval`, `--step-seconds`,
and `--horizon-seconds` values, and the qdisc timeline compiler explicitly
records `fixed_10s_assumed=false`.

For BPF-owned forwarding, the most important design change is not a finer
sampler. It is a stricter semantic update filter:

- RuntimeEpoch should mean forwarding or structural link state changed.
- ParameterEpoch should mean qdisc/BPF parameter state crossed meaningful
  thresholds or link active state changed.
- Observation and horizon replenishment should not force forwarding updates.
- BPF should use generation activation, with current-generation support now,
  next-generation preload next, and multi-generation horizon preload as a
  design-ready target.

## Current Sampling Model

### Current Behavior

Sampling is configured at the experiment/preview CLI boundary:

- `satellite/apps/run_starlink_shell_experiment.py` exposes
  `--sample-interval`, `--event-evaluation-interval`, `--horizon-seconds`,
  `--step-seconds`, and `--slice-count`.
- `satellite/apps/build_starlink_shell_preview.py` resolves an effective sample
  interval from `--sample-interval` or `--step-seconds`; if horizon is supplied,
  it derives slice count as `floor(horizon / interval) + 1`.
- `--event-evaluation-interval` can be finer than the sample interval. The
  preview code caps it with `min(sample_interval, event_evaluation_interval)`.
  Propagation and topology snapshot generation then run at that effective
  event-evaluation interval.
- Observation frames are filtered back to the observation/sample interval.

The result is a mixed model:

- fixed-interval evaluation at the dense event-evaluation cadence;
- event-driven RuntimeEpoch and ParameterEpoch creation from semantic diffs;
- separately scheduled ObservationFrames at a coarser interval;
- live horizon replenishment in continuous runtime.

### Evidence

- CLI sampling knobs:
  `satellite/apps/run_starlink_shell_experiment.py` parses the sampling and
  threshold flags around `--sample-interval`,
  `--event-evaluation-interval`, `--horizon-seconds`, and `--step-seconds`.
- Effective preview sampling:
  `satellite/apps/build_starlink_shell_preview.py::_effective_sampling()`
  chooses `sample_interval` when present, otherwise `step_seconds`, validates it
  is positive, and derives slice count from horizon.
- Dense event evaluation:
  `satellite/apps/build_starlink_shell_preview.py::_effective_event_evaluation_interval()`
  returns `min(sample_interval, event_evaluation_interval)` when a finer
  interval is supplied.
- Propagation:
  `satellite/starlink_shell.py::propagate_starlink_records()` and
  `propagate_planned_shell_records()` create slices by iterating
  `timestamp = start + step_seconds * slice_index`.
- Topology conversion:
  `satellite/topology_pipeline.py::TopologySnapshotPipeline.produce_snapshots()`
  runs orbit producer, visibility model, zone generator, and slicer for each
  orbit slice.
- Temporal conversion:
  `satellite/temporal_planning.py::build_sample_boundary_temporal_plan()` turns
  each propagated slice and snapshot into an `OrbitSample`, classifies it as
  runtime, parameter, or no-op, and later builds `ObservationFrame`s.
- Event queue:
  `satellite/temporal_planning.py::build_temporal_plan()` wraps RuntimeEpochs,
  ParameterEpochs, and filtered ObservationFrames into sorted `TemporalEvent`s.

### Is 10s Semantic Truth?

No. 10s is workload configuration in the long-run command history and some
baselines, not a hard semantic invariant.

Evidence:

- The parser defaults `--step-seconds` to `120.0`, not 10.
- `--sample-interval` defaults to `None`; if absent, the code falls back to
  `step_seconds`.
- Preview and runtime paths validate only positivity, not equality to 10.
- `satellite/docs/skyseed_parameter_qdisc_timeline.md` says ParameterEpoch
  generation is event-time and not fixed 10s.
- `satellite/apps/compile_parameter_timeline_to_qdisc.py` records
  `fixed_10s_assumed=false` and uses artifact activation records as cadence
  source.

### Conversion To Core Artifacts

Current conversion path:

1. `PropagatedOrbitSlice`
   - Produced by Starlink/TLE or planned-shell propagation.
   - Contains `slice_id`, `relative_time_seconds`, `timestamp_utc`, and
     satellite position states.
2. `TopologySnapshot`
   - Built by `TopologySnapshotPipeline`.
   - Uses visibility-filtered active links, zone anchor selection, UT-zone
     membership, nodes, and planner hints.
3. `RuntimeUpdatePlan`
   - Built per snapshot by `UtInternetExperimentPlanner`.
   - Contains UT classification, gateway selection, flow contexts, G/Z return
     intent, labels, paths, and backend-neutral-ish node actions.
4. `OrbitSample`
   - Built for every propagated slice in `build_sample_boundary_temporal_plan`.
5. `RuntimeEpoch`
   - Built for sample 1 and later samples whose runtime trigger reasons are
     non-empty.
6. `ParameterEpoch`
   - Built only when no RuntimeEpoch is emitted for that sample and parameter
     trigger analysis finds threshold/interval drift.
7. `ObservationFrame`
   - Built from every sample in the base plan, then filtered by observation
     interval in `build_temporal_plan`.
8. `TemporalEvent`
   - Created for RuntimeEpoch, ParameterEpoch, and filtered ObservationFrame
     records and sorted by activation time and event priority.

## Current Path Computation

### UT Serving Satellite Selection

Current behavior:

- Zone-level anchor selection chooses `active_serving_satellite_id`.
- UT access attachment is then forced to the zone anchor satellite. If the
  selected anchor is not visible to a UT in that zone, the slicer marks that UT
  unresolved instead of attaching it to a different satellite.

Evidence:

- `ZoneAnchorQualityGenerator` scores zone candidates by:
  `coverage_margin_deg * 10 + elevation_deg - gateway_hop_penalty * gateway_hops`.
- It requires the satellite to satisfy min elevation and zone coverage margin.
- For active service zones, it also requires UT attach visibility when the zone
  policy requires it.
- `ExperimentTopologySlicer` rewrites the active UT access link to the selected
  zone anchor candidate and sets node attachment state to `attached`.

Current limitations:

- Serving satellite is zone-anchor based, not independently selected per UT for
  best UT link quality.
- There is hysteresis, but no explicit minimum dwell time.

### GW Visibility, Default GW, Secondary GW

Current behavior:

- Gateway visibility is link-level line-of-sight. The visibility model selects
  the highest-elevation satellite candidate for each gateway access slot.
- Zone default gateway policy is stored in planner hints under
  `zone_default_gateway_preferences`.
- Default gateway selection uses preferred gateway IDs or regional/gateway IDs
  if present, then shortest satellite path with optional stable preferred order.
- Secondary/special gateway behavior is modeled as prefix override policies,
  usually with `selection_mode=target_affinity_shortest_path`.

Evidence:

- `SemiRealLineOfSightVisibilityModel.evaluate()` filters UT/GW candidates below
  `min_elevation_deg` and picks the highest elevation candidate for access
  links.
- `UtInternetExperimentPlanner._zone_default_gateway_policy()` reads per-zone
  gateway preference hints.
- `_resolve_gateway_path()` builds satellite adjacency from active links, finds
  reachable gateway candidate paths, and selects by shortest hop count with
  stable tie-breaks.
- `_core_flow_contexts()` builds a default flow context and optional special
  prefix override flow contexts.

Current limitations:

- Path cost is hop count first. Distance, delay, bandwidth, loss, and coverage
  margin are not direct gateway path selection costs.
- Secondary gateway behavior is policy/prefix driven, not a general multipath
  ranking model.

### Zone Active Serving Satellite

Current behavior:

- `ZoneAnchorQualityGenerator` owns zone active serving satellite selection.
- It uses zone center geometry, coverage margin, gateway path hops, and
  hysteresis against the previous anchor.
- It emits `anchor_selection` metadata with quality score, margin, hops,
  previous anchor, and change reason.

Evidence:

- Default generator constants:
  `hysteresis_score_delta=12.0`, `coverage_loss_margin_deg=2.0`, and
  `gateway_hop_penalty=3.0`.
- If the previous anchor remains above required margin and the challenger does
  not exceed the score delta, the code keeps the previous anchor with
  `change_reason=hysteresis_keep_current_anchor`.

### G/Z FEC Generation

Current behavior:

- G/Z FECs are generated from `RuntimeUpdatePlan` flow contexts, not from raw
  topology alone.
- Forward FECs group by selected gateway class, serving satellite, forward link
  IDs, and forward labels.
- Return FECs group by selected gateway, zone, and responsible/serving
  satellite return target.
- Diffs compare FEC group signatures between consecutive plans.

Evidence:

- `temporal_planning.gz_fec_plan()` extracts flow contexts from forwarding
  actions.
- `forward_fec_basis` is `selected_gateway_class`.
- `return_fec_basis` is `(selected_gateway, zone, responsible_satellite)`.
- `diff_gz_fec_plans()` reports added, removed, changed, unchanged groups,
  affected nodes, changed zones, and estimated operation items.

### G->Z And Z->G Path Selection

Current behavior:

- G->Z/default egress path uses the flow context satellite path from serving
  satellite to gateway-attached satellite plus gateway access link.
- Z->G/return path uses the reverse link list and return path intent from the
  same flow context.
- Shared return state is grouped by gateway-zone-responsible-satellite target,
  but current Linux compatibility still carries per-flow labels and routes.

Evidence:

- `_flow_context()` computes `forward_link_ids`, `reverse_link_ids`,
  `forward_labels`, `reverse_labels`, and `return_path_intent`.
- `_return_path_intent()` records shared backbone return target,
  candidate shared zone return route, edge local delivery target, state
  ownership, and Linux compatibility metadata.
- `gz_fec_plan()` converts those contexts into `g_forward_fec` and
  `gz_return_fec` groups.

### Path Cost Inputs Today

Used directly:

- satellite hop count between serving satellite and gateway satellite;
- gateway preference/allowlist policy;
- stable preferred gateway order, when selected;
- maximum additional hops for preferred gateway competitiveness;
- zone anchor coverage margin and gateway-hop penalty;
- anchor hysteresis score delta.

Computed but mostly not used for path selection:

- per-link distance;
- delay;
- loss;
- bandwidth;
- routing cost;
- UT/GW elevation, after visibility filtering.

## Current Parameter Computation

### Delay

Current behavior:

- Delay is derived from 3D endpoint distance divided by
  `SPEED_OF_LIGHT_KM_PER_MS`.
- Access and ISL links share the distance-based base delay model.

Evidence:

- `DistanceElevationRuntimeParameterModel._access_link_parameters()` and
  `_isl_link_parameters()` compute `delay_ms = distance_km /
  speed_of_light_km_per_ms`.

### Rate / Bandwidth

Current behavior:

- Access bandwidth increases with normalized elevation, bounded by
  `access_min_bandwidth_mbps`.
- Same-plane and cross-plane ISL bandwidth decrease with distance, bounded by
  per-link-type minimums.

Evidence:

- Access default parameters:
  min 120 Mbps, base 220 Mbps, elevation gain 780 Mbps.
- Same-plane defaults:
  min 900 Mbps, base 2200 Mbps, distance penalty 0.08 Mbps/km.
- Cross-plane defaults:
  min 650 Mbps, base 1800 Mbps, distance penalty 0.09 Mbps/km.

### Loss / Drop

Current behavior:

- Access loss decreases with elevation and is bounded by a minimum.
- ISL loss increases after a distance start threshold and is capped.
- BPF parameter map prototypes convert high loss to drop decisions, but the
  canonical qdisc path treats loss as netem/qdisc parameter.

Evidence:

- Access default minimum loss is 0.005%, with elevation span 0.18%.
- Same-plane loss defaults: base 0.004%, start 900 km, slope 0.00001, max
  0.08%.
- Cross-plane loss defaults: base 0.008%, start 1100 km, slope 0.000015, max
  0.15%.
- `bpf_backend.parameter_action()` returns `drop_loss` only when compiled loss
  reaches the all-loss threshold.

### Routing Cost

Current behavior:

- Routing cost is `round(delay_ms * 100)` in the runtime parameter model.
- It is used as threshold metadata for ParameterEpoch and network mutation
  decisions, not as a direct qdisc field.

Evidence:

- `DistanceElevationRuntimeParameterModel._routing_cost()` returns
  `round(delay_ms * routing_cost_scale)` with default scale 100.
- The qdisc timeline compiler records `routing_cost.qdisc_direct_field=false`.

### Active / Inactive Link State

Current behavior:

- Active links are selected by visibility and topology slicing.
- Dynamic slot state marks every active snapshot link as `occupied=true`.
- Missing target slots become deactivation operations.

Evidence:

- `SemiRealLineOfSightVisibilityModel.evaluate()` emits active links only for
  visible selected access candidates and valid ISLs.
- `dynamic_slot_state_from_snapshot()` creates occupied slot state from
  `snapshot.active_links`.
- `build_network_mutation_plan()` emits activate, deactivate, rebind, or
  update-parameters operations by comparing source and target slot state.

### Qdisc Update Generation

Current behavior:

- ParameterEpochs execute through the host/direct network mutation path as
  `update_slot_parameters`.
- Runtime execution filters ParameterEpochs to parameter-only operations; if a
  ParameterEpoch would require non-parameter operations, it is escalated to
  effective RuntimeEpoch.
- qdisc timeline compilation reads real ParameterEpoch execution details and
  builds per-link qdisc segments.

Evidence:

- The runtime loop builds a network mutation plan for each selected epoch.
- For `classification == parameter_epoch`, it keeps only
  `update_slot_parameters` operations when safe.
- It records `parameter_qdisc_apply_seconds` from update-slot apply results.
- `compile_parameter_timeline_to_qdisc.py` reads
  `host_mutation_execution.apply_results[].resulting_state.runtime_parameters`
  and emits `tc qdisc change ... netem delay ... rate ... loss ...` templates.

## Current Epoch Triggering

### RuntimeEpoch Triggers

Current behavior:

- First sample always emits `initial_runtime_epoch`.
- Later RuntimeEpochs emit when `_runtime_trigger_reasons()` detects:
  - active serving satellite map change;
  - gateway exit summary change;
  - active link topology signature change;
  - planner path change;
  - MPLS/route/label/selected-gateway change.

Evidence:

- Runtime trigger reasons are generated by comparing previous and current
  snapshots and runtime update plans.
- Topology signature includes slot type, binding, endpoints, node IDs, node
  types, interface names, and IP addresses.
- Planner signature includes serving satellite, selected gateway, selected
  gateway satellite, satellite path, forward/reverse link IDs, labels, UT access
  link, and gateway access link.
- FEC diffs are generated as artifacts but are not the direct trigger predicate;
  they are downstream of runtime plan differences.

### ParameterEpoch Triggers

Current behavior:

- ParameterEpoch is considered only when no RuntimeEpoch is emitted for the
  sample.
- It compares current parameters against the last parameter epoch snapshot.
- It emits when any changed link crosses:
  - delay threshold;
  - loss threshold;
  - bandwidth threshold;
  - routing-cost threshold.
- If `parameter_max_interval` elapsed and there are changed links, it adds
  `parameter_max_interval_elapsed`.

Default thresholds from the runner:

- delay: 0.5 ms;
- loss: 0.02 percentage points;
- bandwidth: 5%;
- routing cost: 5;
- parameter max interval: 240 seconds.

Important nuance: `parameter_max_interval_elapsed` does not create a
ParameterEpoch with no changed links. It only adds an interval reason when drift
already exists.

### Fixed Interval With No Semantic Change

Current behavior:

- No RuntimeEpoch or ParameterEpoch is emitted for no-op samples.
- ObservationFrames can still be emitted for reporting/observation.
- The temporal plan records `no_op_sample_count` and
  `samples_compressed_out_of_runtime_updates`.

Evidence:

- `build_sample_boundary_temporal_plan()` sets sample classification to
  `no_op` when neither runtime nor parameter reasons fire.
- `TemporalPlan.to_dict()` reports no-op and compression counts.

### Horizon Replenishment

Current behavior:

- Continuous runtime can replenish the prepared horizon when remaining queued
  activation time drops below `--replenish-threshold-seconds`.
- Replenishment runs a preview-only segment for the next time window, appends
  accepted epoch queue entries, and updates context by segment key.
- It is a queue preparation mechanism, not a semantic trigger reason.

Evidence:

- `_initial_continuous_horizon_seconds()` chooses an initial segment based on
  total horizon, sample interval, and replenish threshold.
- `maybe_replenish()` appends future segment entries when remaining horizon is
  low and total requested horizon has not been produced.

## Implemented Today Versus Design-Only

Implemented today:

- Configurable dense sampling and event-evaluation interval.
- RuntimeEpoch/ParameterEpoch/no-op classification.
- ObservationFrame filtering.
- RuntimeEpoch path/topology/gateway/label diffing.
- ParameterEpoch threshold and max-interval drift detection.
- G/Z FEC plan and diff artifacts.
- BPF shadow/generation map compilers.
- BPF-owned forwarding readiness for bounded eligible satellite-core G/Z scope.
- qdisc timeline compiler for ParameterEpoch artifacts.
- Current-generation BPF activation concepts and fail-closed generation checks.

Design-only in this document:

- Changing RuntimeEpoch trigger predicates.
- Changing ParameterEpoch trigger predicates.
- New BPF-aware scheduler.
- Next-generation preload in live runtime.
- Multi-generation horizon preload in live runtime.
- New cost model that uses delay/bandwidth/loss directly for route selection.
- Minimum dwell-time enforcement.
- Boundary-UT or remote-return policy changes.
- qdisc backend replacement or TheaterQ/custom qdisc integration.

## BPF-Aware Optimization Design

### RuntimeEpoch Semantic Contract

RuntimeEpoch should be emitted only for semantic forwarding or structural link
changes:

- FEC path change:
  - added/removed/changed G forward FEC;
  - added/removed/changed GZ return FEC;
  - changed FEC next-hop/interface/action for the active generation.
- UT serving satellite change:
  - UT ingress attachment changes because zone anchor or UT visibility changes.
- Zone anchor change:
  - `active_serving_satellite_id` changes for an active service zone.
- GW/default/secondary selection change:
  - selected default gateway class changes;
  - special prefix override gateway class changes;
  - selected gateway satellite changes.
- Core G/Z path change:
  - satellite path or forward/reverse link ID sequence changes.
- Real link up/down structural change:
  - active link appears/disappears;
  - slot binding changes;
  - endpoint/interface/IP changes in a way that changes packet delivery.

RuntimeEpoch should not be emitted solely because:

- observation cadence elapsed;
- horizon replenishment ran;
- qdisc-only delay/rate/loss drift crossed a qdisc threshold;
- labels changed only as an artifact of Linux/MPLS compilation while semantic
  FEC path did not change, once BPF-owned mode is active.

### ParameterEpoch Semantic Contract

ParameterEpoch should be emitted only for meaningful parameter changes:

- delay threshold crossing;
- bandwidth/rate threshold crossing;
- loss/drop threshold crossing;
- active/inactive transition for a link or parameter key;
- optional bounded refresh only when required by Linux qdisc drift control and
  only when it changes the qdisc program/state.

ParameterEpoch should include explicit field-level reasons:

- `delay_bucket_changed`;
- `bandwidth_bucket_changed`;
- `loss_bucket_changed`;
- `drop_state_changed`;
- `active_state_changed`;
- `qdisc_refresh_due_to_kernel_state_expiry` if a future backend truly needs it.

### BPF Generation Strategy

Current generation support now:

- Load active generation maps for the currently selected RuntimeEpoch.
- Switch `active_generation` atomically after all maps for the generation are
  installed and verified.
- Preserve fail-closed behavior for missing generation, missing FEC, inactive
  FEC, future generation, inactive parameter, and future parameter generation.

Next-generation preload if feasible:

- Preload the next RuntimeEpoch generation as inactive.
- Verify map completeness and counter initialization before activation time.
- At activation time, update only `active_generation`.
- Keep rollback as generation switch-back where safe, plus cleanup of old maps
  after a grace interval.

Multi-generation horizon preload as design-ready:

- Compile the prepared horizon into a ring or hash of inactive generation maps.
- Store generation metadata with activation time, source sample/slice, FEC
  fingerprint, parameter fingerprint, and rollback parent.
- Garbage-collect generations after:
  - activation plus observation/rollback grace;
  - no live flow references;
  - counter snapshot captured.

### Qdisc Remains Linux-Owned

Because qdisc remains Linux-owned:

- ParameterEpoch frequency still matters.
- BPF-owned forwarding should not increase qdisc churn.
- Delay/rate/bandwidth changes should use coarser buckets or hysteresis than
  pure numeric equality.
- Loss/drop can be split:
  - BPF owns packet drop/pass/counter behavior for active/loss/drop decisions;
  - Linux qdisc owns probabilistic netem loss if fidelity requires it.

Recommended initial split:

- BPF: active gate, hard drop, coarse loss bucket counters, generation/mark.
- qdisc: delay, rate/bandwidth, queueing, probabilistic loss when enabled.
- Routing cost: metadata for route selection and audit, not qdisc direct input.

## Realism Improvements To Consider

### Coverage Margin

Current default is 2 degrees. Proposed policy:

- hard minimum elevation: keep scenario `min_elevation_deg`, often 5 degrees
  in Starlink preview;
- soft coverage margin: 2 to 5 degrees depending on workload scale;
- handover guard: do not switch to a candidate with less than current margin
  unless current anchor is below margin or the candidate improves path quality
  materially;
- expose `coverage_margin_bucket` to avoid churn near the boundary.

### Hysteresis

Current score hysteresis is 12.0. Proposed policy:

- keep score hysteresis, but split it into interpretable components:
  - coverage improvement threshold, e.g. 2 degrees;
  - hop improvement threshold, e.g. at least 1 hop if coverage is similar;
  - path-quality threshold based on delay/routing-cost bucket;
  - gateway preference penalty.
- require challenger to beat current anchor by both score and one explicit
  physical metric unless current anchor is near loss of coverage.

### Minimum Dwell Time

Current code has no explicit dwell-time floor. Proposed policy:

- default minimum dwell: 30 to 60 seconds for zone anchors;
- emergency override: allow immediate handover if current anchor is below min
  elevation or below required margin;
- boundary UT mode: use longer dwell, e.g. 60 to 120 seconds, unless service is
  unavailable;
- record dwell suppressions in `anchor_selection.change_reason`.

### Parameter Delta Thresholds

Current defaults are a reasonable starting point for 10s-ish workloads:

- delay 0.5 ms;
- loss 0.02 percentage points;
- bandwidth 5%;
- routing cost 5;
- parameter max interval 240s with drift.

Proposed BPF/qdisc policy:

- delay: 0.5 to 1.0 ms for access, 1.0 to 2.0 ms for ISL, or bucketized by
  1 ms bins;
- bandwidth: 5% for access, 10% for high-capacity ISLs unless near capacity
  thresholds;
- loss: 0.02 percentage points for low-loss links, but use drop-state thresholds
  separately;
- hard drop: explicit transition only at configured loss/drop boundary, not
  every loss drift;
- routing cost: keep as derived/audit value; trigger route recomputation only
  when route rank changes, not when raw cost changes.

### Path Stability Versus Shortest Path

Current planner is shortest-hop oriented. Proposed policy:

- choose stable path if it is within `N` hops or `X` ms of shortest path;
- default `N=1` additional satellite hop for preferred gateways;
- use a composite rank for future route selection:
  - gateway policy priority;
  - reachability;
  - hop count;
  - delay bucket;
  - bandwidth floor bucket;
  - loss/drop bucket;
  - coverage margin bucket;
  - handover/dwell penalty;
  - stable tie-break by gateway and satellite path.
- only emit RuntimeEpoch when the selected semantic path changes, not when
  non-winning candidate scores fluctuate.

### Boundary UT And Remote Return Handling

Boundary UT policy:

- explicitly tag boundary UTs in catalog or derived workload metadata;
- require stronger hysteresis and dwell before zone handover;
- permit temporary local degradation if the current anchor remains serviceable;
- audit `ut_ids_without_selected_anchor_visibility` as hard failure for active
  service zones, not just metadata.

Remote-return policy:

- distinguish default-near, secondary-near, zone-boundary, and remote-return UT
  roles in planning artifacts;
- prefer stable shared GZ return FECs for remote-return flows;
- avoid changing default gateway and return anchor independently unless both
  changes are part of the same generation;
- require explicit fallback when preferred remote return gateway is unreachable
  or not competitive.

## Proposed Trigger Predicate Sketch

The optimized planner should compute these fingerprints per sample:

- `forwarding_semantic_fingerprint`:
  - UT to zone/anchor map;
  - default and override gateway class bindings;
  - G and GZ FEC groups;
  - core path link IDs and next-hop actions;
  - active structural link membership.
- `parameter_semantic_fingerprint`:
  - per-link delay bucket;
  - bandwidth/rate bucket;
  - loss/drop bucket;
  - active/inactive state;
  - qdisc-owned field values.
- `observation_fingerprint`:
  - reporting-only frame metadata.

Then:

- emit RuntimeEpoch when forwarding fingerprint changes;
- emit ParameterEpoch when parameter fingerprint changes and runtime fingerprint
  did not change;
- emit ObservationFrame on observation cadence;
- replenish horizon without creating synthetic runtime/parameter triggers.

## Blockers And Unknowns

- FEC diff is currently an artifact, not the primary RuntimeEpoch trigger. A
  BPF-owned scheduler should make FEC semantic diff authoritative or prove that
  planner signature diff is equivalent.
- Runtime signatures still include MPLS labels. BPF-owned mode should ignore
  Linux/MPLS label churn when semantic FEC/path state is unchanged.
- ParameterEpoch has a `parameter_max_interval_elapsed` reason today. Whether
  this remains useful depends on qdisc backend behavior and drift tolerance.
- Current route selection does not use delay/bandwidth/loss as first-class
  path costs. Realistic path stability needs a route-ranking policy, not only
  threshold tuning.
- Minimum dwell time is not implemented.
- Boundary UT and remote-return roles exist in workload planning metadata, but
  current anchor/path code does not appear to enforce role-specific handover
  policies.
- qdisc remains a major Linux-owned surface. BPF-owned forwarding can reduce
  node-local forwarding churn, but cannot by itself eliminate qdisc update cost.
- Multi-generation preload needs memory sizing, generation GC, stale generation
  fail-closed tests, and rollback semantics before it can be live.

## Bottom Line

The current Skyseed pipeline already separates dense sample evaluation from
runtime update emission, but the next optimization should make semantic
fingerprints explicit and BPF-aware.

The immediate design target is:

- RuntimeEpoch equals forwarding or structural link semantic change.
- ParameterEpoch equals meaningful parameter bucket/state change.
- ObservationFrame equals reporting cadence.
- Horizon replenishment equals preparation only.
- BPF-owned forwarding activates generations, while Linux qdisc remains a
  carefully throttled parameter backend.
