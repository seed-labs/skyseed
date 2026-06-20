# Skyseed Cost Attribution For The 2h Canonical Run

This note covers Goal 3 cost attribution for the current canonical baseline:
planned-shell `S5` full `1-shell`, `12UT/6GW` valid6, 6-zone metadata catalog,
event-time `RuntimeEpoch`/`ParameterEpoch`, G/Z FEC diff, and the direct backend
with `dynamic_satellite_link=0`.

## Run Source

- Run directory:
  `satellite/output/starlink_shell_experiments/gz-z6metadata-2h-gated-20260611-0824`
- Goal 1 baseline commit:
  `7efef0f389f417cb6d1f42a77ae602fd901470f3`
- Goal 2 platform boundary commit:
  `7e6ec793f0719a0126b53b8675eb43f05c1bdbfe`
- Compact summary:
  `satellite/baselines/skyseed_1shell_6z_2h_cost_attribution_summary.json`
- Analyzer:
  `satellite/apps/analyze_skyseed_cost_attribution.py`

The analyzer reads existing artifacts only. It does not modify G/Z semantics,
runtime behavior, backend implementation, or the run directory.

## Main Timing Conclusions

- The run executed 325 `RuntimeEpoch` records and 275 `ParameterEpoch` records
  across 23 replenishment segments.
- Initial activation was 613.836268s. Most of that was pre-epoch cold start
  and readiness work: 579.663337s before first epoch execution. The first
  runtime transition itself was 34.173132s.
- Timing bucket guardrail: the 613.836268s value is the broad
  `initial_activation_wall_seconds` bucket: cold start plus the first runtime
  execution. The Goal 1 regression baseline's
  `initial_materialization_seconds = 34.173132s` is a narrower bucket: the
  first `RuntimeEpoch` transition only. They are intentionally different
  metrics, and the 34.173132s bucket is the one excluded from steady
  RuntimeEpoch p50/p95/max.
- Steady `RuntimeEpoch` transition time was p50 3.850701s, p95 7.968063s,
  max 15.870726s.
- `ParameterEpoch` transition time was p50 0.820086s, p95 1.284543s,
  max 2.234283s.
- Final rollback/cleanup known wall time was 2667.56364s. It is much larger
  than any steady epoch and deserves first-class optimization attention.

## RuntimeEpoch Cost Attribution

For steady runtime epochs, excluding the initial materialization epoch:

- Host apply was the largest directly visible steady-state component:
  p50 1.924533s, p95 4.397095s, max 8.564896s.
- Node-local apply was smaller but still material:
  p50 0.542257s, p95 1.440595s, max 2.751492s.
- Node-local incremental cleanup was also material:
  p50 0.921908s, p95 2.658735s, max 5.087s.
- Network mutation plan/apply closely tracked host apply:
  p50 1.931714s, p95 4.4037s, max 8.572091s.
- Artifact write and audit write timing marks were effectively zero in these
  rows: both p95 values were 0.000001s or less.

The run recorded 5219 RuntimeEpoch host operations after including the initial
epoch. Operation classes were:

- `update_slot_parameters`: 3771
- `rebind_slot_attachment`: 1255
- `activate_slot_attachment`: 193

G/Z FEC diff visibility is useful but not live-epoch complete. The available
G/Z diff artifact is a 25-slice summary, while live RuntimeEpoch records repeat
slice IDs across replenishment. Joining by `slice_id` gives evidence, but not
independent per-live-epoch FEC recomputation.

Slice-level G/Z FEC totals:

- affected nodes: total 40, max 10
- operation estimate: total 127, max 25, using
  `estimated_lfib_route_operation_items`
- changed G FEC groups: 25
- changed GZ FEC groups: 23
- skipped unchanged FEC groups: 220
- skipped G-vs-GZ split: not available

Correlation with steady RuntimeEpoch host apply:

- per-live-epoch operation count: 0.551014
- slice-joined affected nodes: 0.51387
- slice-joined operation estimate: 0.414143
- slice-joined changed FEC groups: 0.440985
- slice-joined changed GZ FEC groups: 0.447893
- slice-joined skipped FEC groups: -0.593652

This supports a moderate relationship between host apply long tails and both
per-epoch operation count and slice-level FEC change scope. It does not prove a
specific backend command type is responsible.

## ParameterEpoch Cost Attribution

ParameterEpoch records show a clean parameter-only path:

- Transition wall time: p50 0.820086s, p95 1.284543s, max 2.234283s.
- Host apply: p50 0.556252s, p95 0.979891s, max 1.38801s.
- Qdisc cumulative operation timing: p50 1.050617s, p95 1.879972s,
  max 2.581523s.
- Metadata update: p50 0.005945s, p95 0.007967s, max 0.0825s.
- Dirty reconcile: p50 0.000179s, p95 0.000293s, max 0.000764s.

Timing bucket guardrail: `ParameterEpoch` transition p95 and qdisc p95 are
computed over the same 275 live ParameterEpoch records, but they are different
statistical fields. `transition_wall_seconds` is per-epoch elapsed wall time.
`parameter_qdisc_apply_seconds` is built by summing
`qdisc_apply_wall_seconds` across `update_slot_parameters` operation results
inside each epoch. Because those operation timings are cumulative operation
timings rather than a single additive wall-clock sub-bucket, the qdisc p95
can be greater than the transition wall p95. The analyzer therefore reports it
as `qdisc_apply_cumulative_operation_seconds`.

ParameterEpoch changed-link visibility:

- total changed parameter links: 3243
- max changed parameter links per epoch: 21
- p50/p95 changed links per epoch: 11/18
- operation class: `update_slot_parameters` only
- delta category: `changed` only
- slot types: 1868 `ut_access`, 943 `gw_access`, 432 `cross_plane_isl`

Live trigger reasons show:

- `routing_cost_changed`: 275
- `bandwidth_threshold_exceeded`: 156

No live ParameterEpoch escalated to hidden full apply or recovery:

- hidden full apply count: 0
- hidden recovery count: 0

Delay/loss/bandwidth/cost category detail is only partially visible. Live
records expose trigger reasons for routing cost and bandwidth threshold events;
delay/loss counters are not present per live epoch. The compact initial-horizon
`parameter_epoch_summary.json` has `changed_parameter_counts`, but that is not
the same as per-live-epoch category accounting across all replenishment.

## Rollback And Cleanup

Final rollback/cleanup known wall time was 2667.56364s:

- node-local rollback: 1410.550751s
- host rollback: 1155.176739s across 600 rollback records
- host artifact cleanup: 98.722829s
- post-run preflight: 3.113321s

The existing artifacts can further split some of this:

- node-local rollback had 7397 batch records and 40617 logical commands.
- node-local rollback batch wall time was p50 0.177775s, p95 0.280277s,
  max 0.797034s.
- host rollback had 8462 operations. Host rollback wall time was p50 1.54941s,
  p95 3.978594s, max 44.731758s.
- host artifact cleanup removed 1961 host interfaces and 193 bridges, with no
  remaining bridge/interface residue.

The artifacts do not split node-local rollback into typed route/MPLS/NAT
add/change/delete timing, and they do not split host artifact cleanup by
individual interface or bridge deletion timing.

## Instrumentation Gaps

Current artifacts are enough to identify broad hot paths, but not enough to
validate command-level backend optimizations.

- qdisc add/change/delete counts: gap. Available fields show qdisc apply time
  and host slot operation counts, not typed `tc` command counts.
- Linux route/MPLS add/change/delete counts: partial. Node-local batch
  `command_count` and descriptions are present, but typed counters and
  per-command timing are missing.
- veth/netns/link operation counts: partial. Host slot operation counts and
  cleanup removed interface/bridge counts are present, but typed create/delete
  counters per epoch are missing.
- subprocess command count: partial. Node-local batches have command counts;
  host-side subprocess or netlink command counts are not separated.
- Docker exec batch count: partial. Node-local batch records are visible, but
  the artifact does not explicitly separate Docker exec transport count from
  logical node-local batch count.
- artifact write blocking: partial. Epoch timing marks include
  `artifact_write_blocking_seconds` and `audit_write_seconds`, but large JSON
  serialization/write timing is not split by artifact file.
- rollback node/host/artifact split: available at top level, but missing
  command-type timing inside node rollback and per-resource timing inside host
  cleanup.

Recommended next timing marks and counters:

- host backend typed operation counters:
  qdisc add/change/delete, veth create/delete, netns move/link up/down, route
  add/change/delete.
- host backend execution transport counters:
  subprocess count, netlink batch count, retry count, failed command count.
- node-local typed command counters:
  route, MPLS LFIB, MPLS platform label, iptables/NAT, sysctl, policy rule.
- per-command or per-batch timing for node-local apply and rollback.
- artifact serialization/write timing by file:
  `timing.json`, `epoch_execution_details.jsonl`, `real_runtime_report.json`,
  G/Z audit, temporal audit.
- rollback cleanup per-resource timing:
  per interface, bridge, qdisc, route, and namespace cleanup groups.

## Scale Caveat / Future Workload Risk

This 2h run is the canonical stability baseline. It is not the final large-scale
performance stress test.

The current experiment uses 12 UTs, 6 gateways, and 6-zone metadata. The zone
and UT/GW placement is relatively local for this shell, and many zones are near
the selected gateways or UTs. The planned S5 shell used here has 172 satellites
across only 4 planes, so its coverage window is limited. That limit makes the
earlier, more complete zone/GW/UT spatial planning less meaningful for this
specific baseline than it will be for larger shells.

As a result, this run may materially understate future update cardinality.
Larger shells, more complete zone partitioning, more gateways and UTs, and more
spatially dispersed UT placement can all increase the number of affected
runtime objects:

- ParameterEpoch qdisc and parameter update counts may grow substantially.
- RuntimeEpoch affected node counts may grow substantially.
- FEC, route, and slot update counts may grow substantially.

The evidence in this document therefore supports a narrow conclusion: in the
current 12UT/6GW, 6-zone baseline, ParameterEpoch is a clean parameter-only
path. It does not prove that ParameterEpoch cost will remain controlled under a
future large-scale zone/GW/UT distribution.

Future acceptance should add workload and cardinality metrics alongside timing:

- active edge count
- changed parameter link count per ParameterEpoch
- qdisc update count per ParameterEpoch
- affected node count per RuntimeEpoch
- FEC/route operation estimate per RuntimeEpoch
- UT/GW count
- zone count
- spatial distribution and covered window

Final optimization acceptance should not look only at a larger shell satellite
count. It should also validate the number and spatial distribution of zones,
gateways, and UTs because those dimensions can change update cardinality even
when the satellite count alone looks acceptable.

## Optimization Priority

Evidence-backed priority:

1. Rollback/cleanup should be optimized or at least instrumented first. It is
   2667.56364s, with large node-local and host rollback components. The current
   evidence is strong enough to justify work, but command-type timing is still
   needed before choosing between route/MPLS batching, Docker exec reduction,
   and host cleanup changes.
2. Host RuntimeEpoch batching/netlink work is worth prioritizing. Steady host
   apply has p95 4.397095s and max 8.564896s, and correlates moderately with
   operation count. The missing piece is typed host command counters to decide
   whether qdisc, veth/netns, route, or subprocess overhead dominates.
3. ParameterEpoch qdisc optimization is worthwhile after the above or in
   parallel if low-risk. ParameterEpoch is much smaller than RuntimeEpoch and
   rollback, but its visible mutation cost is qdisc-heavy and cleanly isolated
   from hidden full apply.
4. MPLS/G/Z forwarding BPF shadow is not the first steady-state apply target
   based only on this run. Node-local apply is meaningful, and node-local
   rollback is very large, but steady RuntimeEpoch host apply is the larger
   per-epoch component. BPF shadow needs better route/MPLS/NAT command-type
   counters before expected benefit can be estimated.

## Evidence And Hypotheses

Evidence-backed:

- RuntimeEpoch count, ParameterEpoch count, replenishment count, initial
  activation time, steady epoch p50/p95/max, and cleanup split are direct
  artifact measurements.
- RuntimeEpoch host apply, node-local apply, node-local incremental cleanup,
  audit write, and artifact write numbers come from `timing.json`.
- ParameterEpoch qdisc, metadata, dirty reconcile, hidden full apply, and
  hidden recovery conclusions come from live epoch timing/details.
- Rollback split, node rollback batch count, command count, and host rollback
  count come from `real_runtime_report.json`.
- The moderate correlation between host apply and operation/FEC scope is
  computed from live epoch records plus slice-level G/Z diff summaries.

Hypotheses:

- Host apply long tails are likely driven by host mutation operation volume and
  backend command overhead, but the exact command family is not proven.
- qdisc is likely the main ParameterEpoch optimization surface, but the current
  qdisc timing does not expose `tc` add/change/delete or netlink batching.
- Node-local rollback is likely sensitive to route/MPLS/NAT batch strategy and
  Docker exec transport overhead, but the artifact lacks typed command timing.
- BPF shadow may reduce MPLS/G/Z route work, especially rollback work, but this
  run does not yet provide enough backend-operation visibility to quantify it.
