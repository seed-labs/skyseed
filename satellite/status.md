# Satellite Iteration 2 Status

## 2026-06-20 - Isolated G/Z BPF Shadow Prototype

- Goal 8 added an isolated tc-BPF G/Z forwarding shadow prototype:
  `apps/bench_skyseed_bpf_gz_shadow.py`,
  `experimental/bpf/skyseed_tc_gz_shadow.c`,
  `docs/skyseed_bpf_gz_shadow.md`, and
  `baselines/skyseed_bpf_gz_shadow_summary.json`.
- The prototype keeps canonical runtime, G/Z semantics, real Skyseed dataplane,
  `satellite/output`, netlink batch direction, and external kernel modules out
  of scope. It only creates temporary `ssgz*` netns/veth topology and cleans it
  in a `finally` path.
- Validation on this VM: tc-BPF attach succeeded for redirect and shadow
  sections; BPF read `active_generation`; `(generation, FEC)` map lookups drove
  gen0 `FEC_G -> gw1`, gen1 `FEC_G -> gw2`, and gen2 inactive/drop; missing
  FEC, missing generation, and inactive edge all failed closed.
- tc redirect to next-hop was validated by gateway RX counters: gen0 packets
  reached only `gw1`, gen1 packets reached only `gw2`. End-to-end redirected
  ICMP replies did not complete, recorded as
  `end_to_end_ping_reply_failed_after_expected_next_hop_rx`; this is not
  claimed as a complete L3 dataplane.
- Packet counters recorded generation hits, FEC hits, redirect/pass decisions,
  required fail-closed drops, future-generation-not-used, local delivery, and
  gw1/gw2 next-hop decisions. Residue check was clean for test namespaces,
  veths, pinned maps, and tc filter residue possibility.
- Validation commands run:
  `python -m py_compile satellite/apps/bench_skyseed_bpf_gz_shadow.py`,
  `python satellite/apps/bench_skyseed_bpf_gz_shadow.py --format text`, and
  `git diff --check`.
- Next recommended action: build a Skyseed G/Z FEC intent to BPF generation map
  compiler against this isolated schema before considering any canonical
  runtime backend work.

## 2026-06-15 - Stable Runtime Route Cleanup Checkpoint

- The current canonical real-runtime path is planned-shell `S5` full
  `1-shell` (`172` satellites), 6-zone metadata catalog, 12UT/6GW valid6
  catalogs, `--ground-placement catalog`, event-time RuntimeEpoch /
  ParameterEpoch queueing with live replenishment, G/Z FEC diff planning,
  direct backend with SEED `dynamic_satellite_link=0`, ping monitoring,
  first-class timing marks, temporal/return-path audits, rollback, and
  post-run preflight.
- The authoritative full-shell 6-zone evidence remains
  `output/starlink_shell_experiments/gz-z6metadata-2h-gated-20260611-0824`:
  live `7200s` queue passed with `325` RuntimeEpochs, `275` ParameterEpochs,
  `23` replenishments, ping success, shared G/Z backbone evidence, temporal
  audit pass, rollback completion, and clean post-run preflight.
- `skyseed_session_check.py` is the durable new-session handoff. Its canonical
  command profile now pins the 6-zone metadata catalog and the validated
  covered start window, so a new Codex window should not reconstruct the route
  from chat memory.
- 24-zone and 32-zone catalogs remain preview-only exploration. They are useful
  for planner/FEC-diff analysis, but they are not canonical real-runtime paths
  until required-service coverage gaps and broad multi-zone churn are fixed.
- Current cleanup scope: organize entrypoints, docs, generated-log hygiene, and
  lightweight validation. Do not change G/Z semantics, tune coverage margins,
  or start a long real-runtime run as part of this stabilization pass.

## 2026-06-10 - Full-Shell G/Z Long-Run Stability Ramp

- Attempted the requested long-duration ramp for the 172-satellite S5
  full-shell, 12UT/6GW G/Z shared-return topology under `seed310`,
  `PYTHONPATH` unset, Mini Internet IX offset `0`, and
  `dynamic_satellite_link=0`.
- The first `20m` preview passed and produced a `121`-sample/epoch plan with
  `46` RuntimeEpochs and `75` ParameterEpochs. The companion G/Z artifact
  passed with `3` shared return groups, legacy backbone return state `36`
  items, and shared G/Z backbone state `6` items.
- The first `20m` real-runtime health run failed before completing any epoch:
  `gw102:apply batch failed with 4`. Rollback completed, temporal audit stayed
  clean, post-run preflight was clean, and the G/Z artifact still passed. A
  narrow diagnostic-only patch now preserves nested node-local batch failure
  causes in runtime artifacts instead of collapsing them into a plain
  `RuntimeError`.
- A first-epoch diagnostic passed, then a full-shell `240s` multi-slice
  diagnostic also passed:
  `output/starlink_shell_experiments/gz-long-1shell-240s-diagnose-20260610-1055`.
  It executed `13` RuntimeEpochs and `12` ParameterEpochs from a `25`-item
  queue, passed ping, rollback, temporal no-future-state checks, and post-run
  preflight. No horizon replenishment occurred because the requested horizon
  was exactly the initial `240s` segment.
- The `240s` diagnostic G/Z artifact
  `output/gz_scale_12ut6gw_20260610_1035/gz_return_semantics_long_240s_diagnose_20260610_1055.json`
  passed: `zone-near -> stl-950110`, `zone-far -> stl-950125`, shared return
  groups `gw101:zone-near:stl-950110`,
  `gw102:zone-near:stl-950110`, and
  `gw105:zone-far:stl-950125`, with `ut_mpls_egress_count=0`,
  gateway `/32` shared-backbone return routes `18 -> 0`, reverse labels
  `18 -> 3`, gateway-zone FECs `3`, and backbone return state `36 -> 6`.
- The `240s` run showed a readiness blocker for longer validation: startup
  prewarm took `481.869s` (`161.512s` prepare + `320.126s` reconcile), and
  host rollback had `25` host history entries at roughly `41-45s` each before
  clean post-run preflight. Live connectivity was stable, but the teardown
  cost is too high to responsibly extend to `1h` or multi-hour runs before a
  narrow cleanup/rollback optimization.
- A narrow final-teardown optimization was added after that diagnostic:
  final host rollback now uses shared state refresh (`rollback_many`) while
  preserving reverse-order per-entry rollback semantics. Focused validation
  passed under `seed310` with `PYTHONPATH` unset:
  `test_host_rollback_batch`, `test_incremental_runtime_reuse`,
  `test_planner_return_path_intent`, and `test_temporal_run_inspector`
  (`27` tests).
- The optimized `20m` real-runtime health run passed:
  `output/starlink_shell_experiments/gz-long-1shell-20m-real-rollbackopt-20260610-1208`.
  It executed `43` RuntimeEpochs and `70` ParameterEpochs, completed `4` live
  replenishment batches, recorded `113/113` epoch ping successes, passed
  rollback, post-run preflight, and temporal no-future-link/no-future-forwarding
  checks. Activation lag average/p95/worst was `4.115s / 17.671s / 41.204s`;
  transition average/p95/worst was `4.360s / 22.618s / 50.762s`. Path hop
  counts stayed bounded (`max=2`, `>20=0`) and `dynamic_satellite_link=0`.
- The `20m` G/Z artifact
  `output/gz_scale_12ut6gw_20260610_1035/gz_return_semantics_long_20m_rollbackopt_20260610_1208.json`
  passed with `3` shared return groups:
  `gw101:zone-near:stl-950110`, `gw102:zone-near:stl-950110`, and
  `gw105:zone-far:stl-950125`. It preserved shared-state counts:
  legacy backbone return state `36` versus shared `6`, reverse labels `18 -> 3`,
  gateway UT `/32` shared-backbone routes `18 -> 0`, gateway-zone FECs `3`, and
  `ut_mpls_egress_count=0`.
- After an accidental VM reboot, the environment was revalidated and repaired:
  host MPLS modules/sysctl were restored, stale runtime residue was checked, and
  preflight was returned to `ready=true` with no blockers or warnings before the
  next real run.
- The optimized `60m` attempt
  `output/starlink_shell_experiments/gz-long-1shell-60m-real-rollbackopt-postreboot-20260610-1404`
  progressed to event-time `3510s/3600s` but failed with Linux OOM kill before
  final rollback. Kernel evidence: `Out of memory: Killed process 6016
  (python)`, `anon-rss:1791032kB`. Checkpoint evidence before kill: `122`
  RuntimeEpochs, `196` ParameterEpochs, `13` replenishment batches, `318/318`
  epoch ping successes, no stall warnings, temporal checkpoint still showing no
  future active-link or future forwarding-state violation. The run was not
  finalized, so rollback/post-run preflight did not run inside the process.
- Post-OOM scoped cleanup was completed manually without deleting artifacts:
  compose containers went from `230` to `0`, direct-backend host interfaces
  from `1441` to `0`, the runtime lock was removed, and independent
  `skyseed_preflight.py --format json` returned `ready=true` with zero warning
  reasons.
- The partial `60m` G/Z artifact
  `output/gz_scale_12ut6gw_20260610_1035/gz_return_semantics_long_60m_partial_oom_20260610_1404.json`
  still passed with the same shared return groups and shared counts as the
  completed `20m` run. The failure is therefore a long-run memory/report
  retention problem, not a G/Z return-semantics or dataplane-connectivity
  regression.
- Bounded memory/report retention was added without changing G/Z semantics:
  compact epoch records now point to `epoch_execution_details.jsonl`, run
  manifests reference the runtime report artifact instead of embedding it, and
  replenishment context retention is pruned to the accepted live queue.
  Focused validation passed under `seed310` with `PYTHONPATH` set only for the
  local test import path: `test_host_rollback_batch`,
  `test_incremental_runtime_reuse`, `test_planner_return_path_intent`,
  `test_temporal_run_inspector`, and `test_temporal_run_comparator`
  (`28` tests).
- The compact-report `20m` validation
  `output/starlink_shell_experiments/gz-long-1shell-20m-real-contextprune-20260610-1620`
  passed with `43` RuntimeEpochs, `70` ParameterEpochs, `4` live
  replenishments, `113/113` pings, rollback success, post-run preflight clean,
  no future active-link or future-forwarding violation, and G/Z PASS. RSS stayed
  below roughly `398 MiB`; artifact sizes were bounded (`real_runtime_report`
  about `20.5 MiB`, `epoch_execution_details.jsonl` about `13.3 MiB`).
- The post-prune `60m` validation
  `output/starlink_shell_experiments/gz-long-1shell-60m-real-contextprune-20260610-1655`
  passed. It executed `126` RuntimeEpochs and `201` ParameterEpochs from the
  real event-time queue, completed `13` live replenishment batches, recorded
  `738/738` post-transition ping checks plus final ping success, passed rollback
  and post-run preflight, and finalized the temporal audit with no future
  active-link or future-forwarding violation.
- The `60m` G/Z artifact
  `output/gz_scale_12ut6gw_20260610_1035/gz_return_semantics_long_60m_contextprune_20260610_1655.json`
  passed with the same `3` shared return groups and counts: legacy backbone
  return state `36` versus shared `6`, reverse labels `18 -> 3`, gateway UT
  `/32` shared-backbone routes `18 -> 0`, gateway-zone FECs `3`, and
  `ut_mpls_egress_count=0`. Hop counts stayed bounded (`1:5790`, `2:96`,
  no long-hop samples above `4`).
- The `60m` metrics artifact
  `output/gz_scale_12ut6gw_20260610_1035/long_run_60m_contextprune_metrics_20260610_1655.json`
  records activation lag average/p95/worst `2.188s / 12.260s / 35.476s`,
  transition average/p95/worst `3.871s / 14.939s / 45.106s`, strict
  sample-boundary deadline misses `327`, max sampled RSS about `474 MiB`, no
  OOM evidence, and bounded artifact sizes (`real_runtime_report` about
  `58.8 MiB`, `epoch_execution_details.jsonl` about `37.8 MiB`).
- Before the `2h` validation, the VM reboot concern was checked from
  `/home/seed/skyseed`; preflight returned `ready=true` with no blockers or
  warnings:
  Skyseed compose containers/networks `0`, direct-backend host artifacts `[]`,
  runtime locks `[]`, host MPLS ready, and `seed310` selected. The only Docker
  containers present were unlabeled historical `exited` containers, not scoped
  Skyseed residue.
- The `2h` multi-hour validation
  `output/starlink_shell_experiments/gz-long-1shell-2h-real-contextprune-20260610-1854`
  passed and finalized. It executed `243` RuntimeEpochs and `409`
  ParameterEpochs from the real event-time queue, completed `26` live horizon
  replenishment batches, reached the bounded `7200s` event-time horizon, passed
  final ping, rollback, run-local post-run preflight, and temporal audit
  (`no_future_active_link_violation=true`,
  `no_future_forwarding_state_violation=true`, `rollback_completed=true`,
  `queue_exhausted_or_stopped_by_bound=queue_exhausted`).
- The `2h` G/Z artifact
  `output/gz_scale_12ut6gw_20260610_1035/gz_return_semantics_long_2h_contextprune_20260610_1854.json`
  passed with the same bounded shared-return state: `3` G/Z return groups,
  legacy backbone return state `36` versus shared `6`, reverse labels `18 -> 3`,
  gateway UT `/32` shared-backbone routes `18 -> 0`, gateway-zone FECs `3`,
  shared serving-satellite pop entries `3`, and `ut_mpls_egress_count=0`.
  The groups were `gw101:zone-near:stl-950110`,
  `gw102:zone-near:stl-950110`, and
  `gw105:zone-far:stl-950125`.
- The `2h` metrics artifact
  `output/gz_scale_12ut6gw_20260610_1035/long_run_2h_contextprune_metrics_20260610_1854.json`
  records activation lag average/p95/worst
  `1.833s / 10.246s / 37.567s`, transition average/p95/worst
  `3.778s / 14.246s / 47.229s`, strict sample-boundary deadline misses
  `652`, path hop distribution `{1: 11580, 2: 156}`, no hop count above `4`,
  and zero ping-failure events.
- The analysis-ready baseline summary
  `output/gz_scale_12ut6gw_20260610_1035/starlink_long_run_baseline_summary_2h_20260610_1854.json`
  consolidates the durable run artifacts, G/Z semantics artifact, and latest
  current-environment preflight into one compact report. It is generated by
  `apps/summarize_starlink_long_run.py` and is the preferred artifact for
  downstream data analysis.
- The `2h` run also recorded the remaining long-run cost centers: cold startup
  before first epoch execution `555.319s`, pre-epoch host prewarm `440.989s`
  (`146.249s` prepare + `294.553s` reconcile), first RuntimeEpoch transition
  `47.229s`, steady RuntimeEpoch transition average `7.192s`, ParameterEpoch
  transition average `1.652s`, final direct-backend artifact cleanup `72.233s`
  after removing `193` bridges and `1473` host interfaces, and total artifact
  size about `335 MiB`.
- After the later VM reboot concern, current preflight was rerun from
  `/home/seed/skyseed` and found no scoped Skyseed residue: compose containers
  `0`, compose networks `0`, direct-backend host artifacts `0`, runtime locks
  `0`, and only five unlabeled historical exited containers. The reboot did
  reset host MPLS modules/sysctl and bridge netfilter passthrough, so current
  readiness is clean but not prepared for another real MPLS run until the host
  preparation helper is rerun.
- Timing terms for the current baseline:
  - activation lag is the wall-clock delay between the scheduled event-time
    boundary and the start of epoch apply
  - p95 is the nearest-rank 95th percentile over the selected epoch population
  - transition time is the wall time to apply an epoch and complete configured
    validation
  - RuntimeEpoch timings include topology/link/forwarding runtime mutation;
    ParameterEpoch timings cover parameter refresh without topology mutation
  - strict deadline miss means activation lag exceeded the scheduler threshold
    (`0.05s` in current runner reports); it is not equivalent to ping loss
  - RSS growth is sampled process resident memory, while artifact/log growth is
    durable bytes written under the run directory
- Current long-run conclusion: the requested 20m -> 60m -> multi-hour
  full-shell G/Z stability ramp has passed after the bounded report/context
  retention fix. The system is ready semantically for further multi-hour
  validation, but the next practical target should be reboot-aware host
  preparation plus node-local/final cleanup wall time and RSS/artifact growth,
  not another G/Z semantic rewrite.

## 2026-06-10 - 12UT/6GW Full-Shell G/Z Scale Validation

- 12UT/6GW full-shell G/Z validation passed on the 172-satellite S5 planned
  shell with `dynamic_satellite_link=0` and SEED Mini Internet IX offset `0`.
  The authoritative run is
  `output/starlink_shell_experiments/gz-scale-12ut6gw-172-eventtime-paramseq-real-20260610-1100`.
- The distinct prefix override used near-zone UTs `ut101..ut106`: default
  gateway `gw101`, `10.160.0.0/24` override gateway `gw102`. The far-zone
  UTs `ut107..ut112` defaulted to `gw105`; their configured override was
  rejected as excessive versus default, so no far override forwarding was
  installed.
- Zone assignments were `zone-near -> stl-950110` for `ut101..ut106` and
  `zone-far -> stl-950125` for `ut107..ut112`; all UTs were attached to and
  visible from their responsible satellite at activation time.
- Shared G/Z return groups were
  `gw101:zone-near:stl-950110`,
  `gw102:zone-near:stl-950110`, and
  `gw105:zone-far:stl-950125`. The G/Z artifact reports legacy backbone
  return state `36` items versus shared G/Z backbone state `6` items, with
  `18 -> 3` reverse labels, `18 -> 0` gateway UT `/32` shared-backbone routes,
  `3` gateway-zone FECs, and `ut_mpls_egress_count=0`.
- Default and override forward paths differed in the live path artifact:
  `ut101 -> stl-950110 -> gw101` for ordinary traffic and
  `ut101 -> stl-950110 -> gw102` for the `10.160.0.0/24` override. Return
  labels were grouped by G/Z target (`200000`, `200256`, `200512`) rather than
  per UT.
- The bounded full-shell event-time run executed three `RuntimeEpoch`s
  (`slice-0001`, `slice-0002`, `slice-0004`) and one real `ParameterEpoch`
  (`slice-0005`) without escalation, passed ping, rollback, temporal
  no-future-link/no-future-forwarding audits, and both run-local plus final
  workspace preflight checks.
- Evidence artifacts:
  `output/gz_scale_12ut6gw_20260610_1035/gz_return_semantics_12ut6gw_172_eventtime_paramseq_real.json`,
  `epoch_execution_queue.json`, `real_runtime_report.json`,
  `path_observation_results.json`, `ping_results.json`,
  `temporal_audit.json`, `run_manifest.json`, `preflight.json`, and
  `post_run_preflight.json` under the run directory above.

## 2026-06-10 - Distinct Prefix Override G/Z Validation

- Added explicit distinct-prefix-override checks to
  `apps/inspect_gz_return_semantics.py`: the artifact now reports whether a
  configured override resolves to a gateway distinct from the UT default, and
  whether default and override forward paths differ.
- Distinct override preview artifact:
  `output/gz_distinct_override_20260610_1010/gz_return_semantics_distinct_preview.json`.
  It validates `ut201`/`ut202` default `gw201` and `10.160.0.0/24` override
  `gw202`, with shared return groups
  `gw201:zone-override-a:stl-950018` and
  `gw202:zone-override-a:stl-950018`.
- Small real distinct override validation:
  `output/starlink_shell_experiments/gz-distinct-override-small-real-ix0-20260610-1015`
  passed real apply, default/override ping validation, rollback, temporal
  no-future-state checks, post-run preflight, and `dynamic_satellite_link=0`.
- Bounded event-time runtime validation:
  `output/starlink_shell_experiments/gz-distinct-override-eventtime-real-ix0-20260610-1020`
  ran the `event_time_queue`, executed one `RuntimeEpoch` and one
  `ParameterEpoch`, preserved shared G/Z return counts, passed ping and
  rollback, and left post-run preflight clean.
- Environment note: these distinct override runs deliberately used
  `--mini-internet-ix-offset 0` so the AS160 target exchange `103` matched the
  override gateway `gw202` peering at IX103. The earlier offset-100 preview
  correctly showed why the override degraded to `resolved_same_as_default`.

## 2026-06-10 - G/Z Shared MPLS Return Semantics

- Implemented the semantic round for shared G/Z return state: zone return FECs
  are aggregated by zone in `starlink_shell_topology.py`, and
  `apps/inspect_gz_return_semantics.py` now emits a dry-run proof artifact for
  default gateway choice, optional prefix override resolution, zone responsible
  satellite assignment, UT->G forward path, GW->Z shared return path, Z pop, UT
  access delivery, and before/after state counts.
- Focused validation passed under `seed310` with `PYTHONPATH` unset to avoid
  importing the legacy `/home/seed/seed-emulator` package. Unit scope:
  `test_starlink_shell_topology`, `test_incremental_runtime_reuse`, and
  `test_planner_return_path_intent` (`17` tests).
- Evidence artifacts:
  `output/gz_return_semantics_synthetic_20260610_084328` (prefix override
  resolved through a distinct gateway),
  `output/gz_return_semantics_starlink_small_preview_20260610_084606`
  (2 same-zone UTs share one GW/Z return group), and
  `output/gz_return_semantics_12ut6gw_preview_20260610_0905`
  (12UT/6GW G/Z default semantics; configured overrides resolved same-as-default
  in that generated layout).
- Real validation artifacts:
  `output/starlink_shell_experiments/gz-shared-small-real-fixedtime-20260610-084709`
  passed apply, ping, rollback, post-run cleanliness, temporal no-future-state
  checks, and `dynamic_satellite_link=0`; same-zone UTs used shared GW/Z MPLS
  return state, with UT delivery kept as local/access IP routing at Z.
- Bounded live horizon replenishment:
  `output/starlink_shell_experiments/gz-shared-replenish-small-real-shifted-h80-20260610-0922`
  passed apply, ping, rollback, and post-run cleanliness; it executed the
  initial precomputed queue, computed/appended a future replenished batch, and
  executed a replenished runtime epoch while preserving temporal no-future-link
  and no-future-forwarding-state checks.

## Current Baseline

- The active optimization target is the `1-shell` Starlink live temporal
  runtime on the direct backend, not the older small-scale Iteration 2 demo.
- Preserve the active contract path:
  `OrbitSample -> TopologySnapshot -> Planner -> RuntimeUpdatePlan -> NetworkMutationPlan -> Runtime`.
- Preserve `RuntimeUpdatePlan != NetworkMutationPlan`, keep algorithm/policy
  boundaries replaceable, and keep dynamic satellite links off Docker compose
  networks.
- Real runs must use runner-enforced host prepare, scoped preflight, runtime
  lock, scoped cleanup, and post-run preflight under the `seed310`
  environment.

## Current Goal Continuation Notes

- After the VM reboot, `skyseed_preflight` is `ready=True` under `seed310`
  with no scoped containers, networks, host interfaces, or runtime locks left
  behind. The remaining warnings are non-blocking host MPLS and bridge
  netfilter readiness signals.
- New-session / post-reboot recovery should start with the same fixed check
  sequence: `git status --short --branch`, `skyseed_preflight.py --format text`
  under `seed310`, focused unit tests, `py_compile` of touched runtime/audit
  modules, then artifact inspection via `inspect_return_path_scaling.py`,
  `inspect_temporal_run.py`, and `compare_temporal_runs.py`. A new long
  real-runtime run should only be spent after those checks are clean and host
  prepare has repaired MPLS/bridge warnings.
- The fixed check sequence is now wrapped by
  `satellite/apps/skyseed_session_check.py`. By default it is read-only and
  prints git status, live goal readiness, and live preflight; `--write-report`
  writes a JSON checkpoint, and `--append-status-note` appends a compact
  session log to this file when an operator intentionally wants a durable note.
- The `1-hop` post-transition behavior in the near-GW validation should be
  treated as a geography-driven ideal case, not as a general sparse-shell
  guarantee. Preview-only near/mixed/far catalog checks have already shown
  path diversity outside the near-GW layout.
- Preview-only distance-matrix artifact inspection is now repeatable via
  `satellite/apps/inspect_starlink_distance_matrix.py`. It reads existing
  `planner_preview.json`, `runtime_update_plan_summary.json`, and
  `ground_manifest.json` artifacts and reports runtime snapshot coverage,
  ISL-hop mean/max, satellite path length mean/max, runtime action counts,
  node-subplan counts, and selected gateway distribution without generating
  new previews or running real runtime.
- Latest distance-matrix inspector output on the three current artifacts:
  - `goal-1shell-172-preview-distance-near6-catalog`:
    `covered_uts=12/12`, `isl_hops_avg=0.0`, `isl_hops_max=0`,
    `runtime_actions_avg=72.0`, `node_subplans_avg=24.0`, gateways evenly
    distributed across `gw1..gw6`.
  - `goal-1shell-172-preview-distance-mixed6-catalog`:
    `covered_uts=6/12`, `isl_hops_avg=5.380952`, `isl_hops_max=20`,
    `runtime_actions_avg=74.285714`, `node_subplans_avg=33.0`, gateways
    `gw1:22`, `gw5:14`, `gw6:6`.
  - `goal-1shell-172-preview-distance-far12-catalog`:
    `covered_uts=10/12`, `isl_hops_avg=5.340909`, `isl_hops_max=24`,
    `runtime_actions_avg=112.0`, `node_subplans_avg=52.888889`, gateways
    `gw1:35`, `gw2:9`, `gw3:9`, `gw4:11`, `gw5:5`, `gw6:19`.
  This makes the near-GW `1-hop` behavior reproducibly attributable to
  geography rather than a hard-coded one-hop planner/runtime special case.
- Return-path scaling is now explicitly audited as a first-class artifact:
  `return_path_audit.json` records the full planner snapshot scope and the
  selected `RuntimeEpoch` scope separately.
- Latest selected-scope smoke:
  `goal-1shell-172-preview-return-audit-selected-smoke`.
  It has `3` planner snapshots / `36` total flows, with `2` selected runtime
  snapshots / `24` runtime flows. Current Linux realization still has
  `per_flow_state=True`, while `shared_reduction_available=True` shows where
  zone/gateway-zone aggregation can reduce return-path state. The same audit
  now separates backbone-return state from edge/local delivery state: selected
  runtime scope has `48` current backbone-return items versus `12` shared
  gateway-zone/zone return targets, a `4.0x` current-to-target ratio.
- The runtime installer now has a first explicit zone-return boundary without
  changing dataplane behavior: `linux_mpls_runtime.py` derives
  `ZoneReturnInstallIntent` groups from `DirectGatewayExperimentContext` by
  `(gateway, zone, serving satellite)`. This proves the installer can see the
  future shared return targets, while emitted Linux commands still use the
  current per-UT `/32` return routes and per-flow reverse LFIB compatibility
  path.
- The planner-to-installer chain is now test-protected: a
  `TopologySnapshot -> Planner -> RuntimeUpdatePlan -> DirectGatewayExperimentContext`
  test verifies that planner `return_path_intent` survives into runtime context
  construction and becomes a `ZoneReturnInstallIntent` with the expected
  gateway/zone/serving-satellite target key.
- The planner return-path contract now makes the future ownership split
  explicit without changing Linux dataplane behavior:
  `shared_backbone_return_target` names the gateway/zone/serving-satellite
  return target owned by the satellite backbone, while
  `edge_local_delivery_target` names the serving-satellite local UT delivery
  target. `linux_mpls_runtime.py` and `return_path_audit.py` now prefer the
  explicit shared target when deriving gateway-zone return keys, but the current
  installer still reports `current_linux_realization.mode=per_ut_flow_compatibility`.
- The shared-target preference is now regression-tested across both sides of
  the boundary: return-path audit and runtime installer grouping collapse two
  flows onto one explicit `shared_backbone_return_target` even when older
  compatibility fields still carry different per-flow gateway/zone/serving-sat
  values. This protects the intended migration direction where shared backbone
  return state is authoritative and UT-specific delivery remains edge-local.
- Return-path audit now includes a machine-readable migration blocker list for
  the current Linux compatibility realization. Each snapshot records the
  current owner/scope/model, item count, target owner/scope/model, and migration
  action for gateway UT `/32` return routes, reverse LFIB labels, reverse
  transit LFIB entries, serving-satellite UT `/32` delivery routes, and gateway
  NAT/FORWARD/SNAT policy rules. Aggregates, inspector, and comparator summaries
  expose `migration_blockers` so old and new artifacts both show which
  per-UT/per-flow install points still prevent shared return dataplane semantics.
- Migration blocker aggregation now also records per-blocker
  `total_current_item_count`, `max_current_item_count_per_snapshot`, and
  `snapshot_count_present`, plus a `top_migration_blocker` summary. On
  `goal-1shell-172-preview-return-audit-selected-smoke`, the old artifact
  backfills to `migration_blockers=4` and
  `top_migration_blocker=gateway_nat_forward_rules:108`, making the largest
  remaining compatibility surface visible before any shared-return dataplane
  rewrite.
- Migration blockers are now categorized so gateway edge policy volume does
  not obscure the satellite-backbone return problem. Audit, inspector, and
  comparator summaries distinguish the global `top_migration_blocker` from
  `top_backbone_migration_blocker`; on the selected-smoke artifact these are
  `gateway_nat_forward_rules:108` globally and
  `reverse_lfib_label_stack:36` for backbone return state. This keeps the next
  design step focused on shared zone return reachability while still tracking
  gateway edge policy separately.
- The same selected-smoke artifact now reports category totals in all three
  reporting paths. Full planner scope has `backbone_return_items=72`,
  `gateway_edge_policy_items=108`, and `edge_local_delivery_items=36`;
  selected runtime scope has `backbone_return_items=48`,
  `gateway_edge_policy_items=72`, and `edge_local_delivery_items=24`.
- Return-path audit now also emits `return_path_migration_readiness`, making
  the current-versus-target return semantics machine-readable instead of only
  implicit in item counts. The selected-smoke recomputation reports
  `shared_backbone_ready=False`, `backbone_migration_required=True`, and
  `backbone_blocker_items=48` for selected runtime scope, with current
  realization `per_ut_flow_compatibility` and target realization
  `shared_gateway_zone_or_zone_return_reachability`. This explicitly preserves
  the conclusion that GW->UT return has not yet been migrated to shared
  satellite-backbone zone reachability.
- `inspect_temporal_run.py` and `compare_temporal_runs.py` now backfill and
  expose the same return-path readiness fields when reading older
  `return_path_audit.json` artifacts directly. On
  `goal-1shell-172-preview-return-audit-selected-smoke`, temporal inspection
  now reports full-scope `return_path_backbone_blocker_items=72` and selected
  runtime-scope `selected_runtime_backbone_blocker_items=48`, with both scopes
  still `shared_backbone_ready=False` and `backbone_migration_required=True`.
  The selected-scope backfill intentionally uses current backbone item count
  when old artifacts have current/target totals but no category totals, avoiding
  a false `0` blocker-item reading.
- Runtime reporting is also prepared for the next real run: `inspect_temporal_run.py`
  and `compare_temporal_runs.py` summarize `zone_return_install_boundary`
  records from `real_runtime_report.json` when present, while older preview or
  real artifacts without that boundary still inspect cleanly with
  `present=False`.
- The installer-side boundary is now self-describing: new runtime sessions
  record `scale_summary` for all active and reapplied flows, including current
  per-flow compatibility backbone items, target shared backbone items, and the
  current-to-target ratio. This lets a future real run compare runtime/session
  behavior against the planner-side `return_path_audit.json` numbers directly.
- Return-path audit now also includes scale projection for `1000` and `10000`
  flows under two explicit assumptions: existing gateway-zone/zone target count
  stays constant, or target count scales at the observed target/flow ratio.
  On `goal-1shell-172-preview-return-audit-selected-smoke`, selected runtime
  projection at `10000` flows is `20000` current per-flow compatibility
  backbone items versus `12` constant shared targets, a `1666.666667x` ratio.
- Timing classification is now test-protected: `_timing_analysis` explicitly
  reports `source_runtime_epoch_count` and
  `excluded_initial_runtime_epoch_count` for steady RuntimeEpoch metrics, and
  tests verify the first heavy RuntimeEpoch is not included in steady averages.
  The inspector also backfills those two fields for older artifacts; on
  `goal-1shell-172-z6valid6-0400-real1` it reports `source_count=6`,
  `excluded_initial=1`, `steady_count=5`.
- Timing analysis now also exposes an explicit `initial_activation` bucket for
  the cold-start plus first-RuntimeEpoch cost. This bucket uses
  `cold_start_before_first_epoch_execution` plus the first RuntimeEpoch
  execution time after direct-backend prepare/reconcile, so prepare/reconcile
  is not double-counted. Older artifacts are backfilled by
  `inspect_temporal_run.py`; on `goal-1shell-172-z6valid6-0400-real1` it
  reports `initial_activation_wall=586.899462`,
  `initial_activation_cold_start=536.65399`, and
  `initial_activation_first_runtime_execution=50.245472`, while steady
  RuntimeEpoch remains `count=5`, `wall_avg=5.46845`, and
  `host_apply_avg=1.712016`.
- Teardown/rollback is now a separate report bucket too. `inspect_temporal_run.py`
  summarizes node-local rollback, host rollback, host artifact cleanup, and
  post-run preflight from existing `real_runtime_report.json` / `timing.json`
  data, and `compare_temporal_runs.py` carries those fields into run
  comparisons. On `goal-1shell-172-z6valid6-0400-real1`, known teardown cleanup
  wall time is `407.523954s`: node-local rollback `55.749114s`, host rollback
  `343.190037s` across `13` host rollback records, and post-run preflight
  `8.584803s`, with cleanup success and `0` remaining host interfaces. Host
  artifact cleanup still lacks a direct wall-time field, so the total is
  deliberately labeled `known_teardown_cleanup_wall_seconds`.
- Future real runs will record `host_artifact_cleanup.timing.cleanup_wall_seconds`
  inside `final_cleanup`, allowing inspector/comparator output to set
  `teardown_cleanup_wall_time_complete=True` and report full
  `teardown_cleanup_wall_seconds`. Older artifacts such as
  `goal-1shell-172-z6valid6-0400-real1` remain compatible and now explicitly
  show `teardown_cleanup_wall=None`, `wall_complete=False`, and
  `host_artifact_cleanup=None` rather than implying a complete teardown total.
- RuntimeEpoch post-apply validation scope is also guarded: CLI defaults,
  installer defaults, and empty-scope fallback now share
  `DEFAULT_RUNTIME_EPOCH_VALIDATION_SCOPE`, with tests pinning the default to
  `reapplied_gateway_class_sample` instead of `all_active_flows`.
- RuntimeEpoch ping validation is now auditable at artifact level too:
  `_select_runtime_ping_validation_contexts` records active flow count,
  reapplied flow count, sample-source flow count, selected ping flow count,
  gateway-class count, reduction count, and selected/source ratio. New runtime
  reports therefore show whether `reapplied_gateway_class_sample` actually
  sampled representative gateway classes instead of validating every active
  flow.
- `inspect_temporal_run.py` now summarizes runtime ping validation selections
  from `real_runtime_report.json` with `present`, record count, scopes,
  sample sources, active/reapplied/source/selected maxima, reduction maxima,
  selected/source ratio, and fallback reasons. Older artifacts without this
  selection inspect cleanly as `present=False`.
- Incremental runtime reuse is now auditable at the same report level:
  runtime session `target_slice` records active, reapplied, held, and
  incremental-cleanup flow counts plus reapplied/active and held/active ratios.
  `inspect_temporal_run.py` and `compare_temporal_runs.py` summarize those
  fields so future real runs can show directly whether RuntimeEpochs are
  reapplying only changed flows instead of reinstalling every active flow.
- The comparator path is now test-protected as a real-run comparison gate:
  `test_temporal_run_comparator.py` verifies that compare summaries include
  runtime ping-validation sampling fields and incremental-reuse fields, so
  future before/after long-run comparisons can catch regressions toward
  all-active-flow ping validation or full-flow reinstallation.
- Failure cleanup sessions now carry the same runtime audit payloads as
  successful sessions: `target_slice` includes active/reapplied/held/cleanup
  flow counts and ratios, and `preflight` includes the chosen
  `ping_validation_selection`. This makes failed real runs useful evidence
  for whether the runtime had stayed on sampled validation and incremental
  reuse before rollback.
- Inspector tests now cover a failed-runtime nested cleanup-session shape, so
  the recursive artifact reader must keep finding incremental-reuse and
  ping-validation summaries even when they are recorded under an error or
  rollback payload rather than a successful `node_local_execution`.
- Latest new-session hygiene check after VM reboot:
  `PYTHONPATH= /home/seed/miniconda3/envs/seed310/bin/python satellite/apps/skyseed_preflight.py --format text`
  returned `ready=True`, with zero scoped containers, networks, direct-backend
  interfaces, and runtime locks. The non-blocking warnings were
  `host_mpls_not_ready` and `bridge_netfilter_passthrough_not_ready`.
- Latest static validation after the migration-category reporting update:
  `PYTHONPATH=/home/seed/skyseed/satellite/apps:/home/seed/skyseed/satellite /home/seed/miniconda3/envs/seed310/bin/python -m unittest satellite/test_return_path_audit.py satellite/test_planner_return_path_intent.py satellite/test_incremental_runtime_reuse.py satellite/test_temporal_run_inspector.py satellite/test_temporal_run_comparator.py satellite/test_timing_analysis.py satellite/test_runtime_validation_defaults.py`
  passed `36` tests when including
  `satellite/test_starlink_distance_matrix_inspector.py`. `py_compile` also
  passed for the touched planner/runtime,
  audit, inspector, runner, and preflight modules.
- Existing preview artifact smoke still works after the inspector summary
  change:
  `inspect_temporal_run.py satellite/output/starlink_shell_experiments/goal-1shell-172-preview-return-audit-selected-smoke --format text`
  reports `Runtime ping validation: present=False` and
  `Runtime incremental reuse: present=False` for that older preview artifact
  while preserving the selected return-path audit summary. The same old
  artifact now backfills and reports `migration_blockers=4` from existing
  `snapshot_reports.per_flow_return_state`. Comparator text smoke on the same
  artifact also includes the migration-blocker, ping-validation, and
  incremental-reuse fields.
- `inspect_temporal_run.py`, `inspect_return_path_scaling.py`, and
  `compare_temporal_runs.py` should now be the default reporting path before
  deciding whether to spend another long real run.
- `satellite/apps/inspect_goal_evidence.py` now combines the goal-facing
  evidence into one preview/artifact-only report: near/mixed/far path
  diversity, return-path migration worklist, runtime validation/reuse
  protections, timing classification, and the current real-run gate. With live
  preflight it reports `near_one_hop=True`, `varied_multihop_runs=2`,
  `one_hop_is_geography_specific=True`; the far preview reaches
  `isl_hops_max=24`, so the old near-GW `1-hop` outcome is not treated as a
  hard-coded sparse-shell guarantee. The same report records selected return
  path scope `selected_flows=24`, `current_backbone_items=48`,
  `target_backbone_items=12`, `ratio=4.0`, and
  `top_backbone_work_item=reverse_lfib_label_stack`, while timing remains
  separated as `initial_wall=586.899462`, steady RuntimeEpoch
  `steady_count=5`, `steady_wall_avg=5.46845`, and ParameterEpoch
  `parameter_wall_avg=1.136802`.
- The same consolidated evidence now includes an explicit directional
  isolation summary. On the selected-smoke artifact, forward UT->GW evidence is
  represented by `forward_model=ut_edge_gateway_class_selection`,
  `forward_gateway_classes=12`, and `forward_flow_to_gateway_class_ratio=2.0`,
  while the GW->UT return side remains
  `return_model=per_ut_flow_compatibility` with
  `return_items_per_flow=2.0`. The selected-scope 10000-flow projection is
  `projection_10000_current=20000` current compatibility backbone items versus
  `projection_10000_target_constant=12` shared targets, a
  `1666.666667x` ratio. This makes the asymmetric state problem explicit:
  UT-side gateway-class choice is comparatively bounded, while return-path
  compatibility state is still the scale risk.
- The readiness checks are now also consolidated in
  `satellite/apps/inspect_goal_readiness.py`. With the current default
  artifacts and live preflight it reports `static_evidence_ready=True` and
  `long_real_run_ready=False`: distance diversity, return-path blocker audit,
  runtime validation/reuse protections, and timing classification are present,
  but host runtime readiness is still blocked by the expected
  `host_mpls_not_ready` and `bridge_netfilter_passthrough_not_ready` warnings.
  This should be the final gate before spending another long real-runtime run:
  run the script once without live preflight for artifact review, then with
  `--live-preflight` immediately before host prepare / real run.
- Return-path migration blockers are now anchored to concrete Linux runtime
  install points via `satellite/return_path_install_audit.py` and
  `satellite/apps/inspect_return_path_install_points.py`. The current audit
  reports `5` blockers and `18` install/preflight/rollback points in
  `satellite/linux_mpls_runtime.py`: `3` backbone-return blockers with `10`
  install points, `1` serving-satellite edge-local delivery blocker with `3`
  install points, and `1` gateway edge-policy blocker with `5` install points.
  `inspect_goal_readiness.py` now includes this as
  `install_points_ready=True`, so the static gate proves not only that return
  path blockers exist in artifacts but also where the current compatibility
  installer realizes them.
- `satellite/apps/inspect_return_path_migration_worklist.py` now combines
  `return_path_audit.json` blocker counts with the Linux install-point audit.
  On `goal-1shell-172-preview-return-audit-selected-smoke`, both full and
  selected runtime scopes rank `reverse_lfib_label_stack` as the top backbone
  work item, followed by `gateway_ut32_return_route`. The selected runtime
  scope reports `24` inferred reverse-LFIB backbone items and `24` inferred
  gateway UT `/32` return-route items against `12` shared target items; the
  inference is explicitly labeled because the older selected-scope artifact
  stores aggregate current/target backbone totals but not per-blocker details.
  Gateway NAT/FORWARD remains visible but deliberately ranks after backbone
  return work so gateway edge policy volume does not obscure the satellite
  backbone migration path.
- `return_path_audit.py` now also emits a
  `shared_backbone_return_reachability_preview` for each snapshot and aggregate
  scope. This is still an artifact-level planning/audit model, not a Linux
  dataplane change: it groups flows by gateway-zone return target and compares
  current per-flow reverse labels with candidate shared return label stacks.
  On `goal-1shell-172-preview-return-audit-selected-smoke`, selected runtime
  scope has `24` current reverse labels versus `12` candidate shared label
  stacks, so `shared_label_ratio=2.0` and reduction is available. The
  readiness gate now surfaces this directly next to
  `top_backbone_blocker=reverse_lfib_label_stack:36`.
- Return-path shared-label audit now also reports whether candidate label-stack
  grouping came from explicit planner metadata or from fallback reverse-path
  inference. `shared_backbone_return_reachability_preview` includes
  `explicit_candidate_metadata_flow_count`,
  `fallback_reverse_path_signature_flow_count`,
  `candidate_metadata_coverage_ratio`, and `candidate_metadata_complete`; the
  readiness gate renders the selected-scope coverage. On the current older
  selected-smoke artifact, `candidate_metadata_coverage=0.0` and
  `candidate_metadata_complete=False`, which is expected and useful: it shows
  the `shared_label_ratio=2.0` result is a valid fallback audit of old planner
  output, not yet proof that new planner-generated candidate metadata covers
  the artifact.
- The planner-to-runtime metadata boundary now carries the next shared
  reverse-label migration handle without changing Linux dataplane behavior.
  `return_path_intent.candidate_shared_return_label_stack` records the
  gateway-zone return target key, reverse-path signature, current compatibility
  reverse labels, and `install_behavior=not_installed_current_linux_compatibility_metadata_only`.
  `linux_mpls_runtime.py` preserves that metadata into the runtime
  `zone_return_install_boundary` report as candidate shared label-stack counts
  and reverse-label-to-candidate ratios. `inspect_temporal_run.py` and
  `compare_temporal_runs.py` now expose those fields for future real-run
  comparisons, while the current installer still emits per-UT/per-flow Linux
  commands.
- The same metadata-only boundary now also names the future zone-route side of
  GW->zone return. `return_path_intent.candidate_shared_zone_return_route`
  records a `gateway_zone_return_route_candidate` with
  `route_scope=zone_or_gateway_zone_return_reachability`,
  `current_compatibility_route_scope=ut_32_return_route`,
  `target_route_scope=zone_or_gateway_zone_return_target`, and
  `install_behavior=not_installed_current_linux_compatibility_metadata_only`.
  `linux_mpls_runtime.py` carries candidate zone-route counts and keys into
  `zone_return_install_boundary`; `return_path_audit.py`,
  `inspect_temporal_run.py`, and `compare_temporal_runs.py` now surface those
  counts. This makes the desired transition explicit: GW return should move
  from per-UT `/32` compatibility routes toward shared zone/gateway-zone return
  reachability, while local UT delivery stays at the serving-satellite edge.
- Latest post-reboot validation after adding candidate-metadata coverage, the
  fixed session-check wrapper, and the consolidated goal-evidence report:
  `PYTHONPATH=/home/seed/skyseed/satellite/apps:/home/seed/skyseed/satellite /home/seed/miniconda3/envs/seed310/bin/python -m unittest satellite/test_return_path_audit.py satellite/test_return_path_install_audit.py satellite/test_planner_return_path_intent.py satellite/test_incremental_runtime_reuse.py satellite/test_temporal_run_inspector.py satellite/test_temporal_run_comparator.py satellite/test_timing_analysis.py satellite/test_runtime_validation_defaults.py satellite/test_starlink_distance_matrix_inspector.py satellite/test_goal_readiness_inspector.py`
  plus `satellite/test_skyseed_session_check.py` and
  `satellite/test_goal_evidence_inspector.py` passed `44` tests.
  `py_compile` also passed for
  `satellite/planner.py`,
  `satellite/linux_mpls_runtime.py`,
  `satellite/return_path_audit.py`,
  `satellite/temporal_run_inspector.py`,
  `satellite/temporal_run_comparator.py`,
  `satellite/return_path_install_audit.py`,
  `satellite/apps/inspect_return_path_install_points.py`,
  `satellite/apps/inspect_return_path_migration_worklist.py`,
  `satellite/apps/inspect_goal_readiness.py`,
  `satellite/apps/skyseed_session_check.py`,
  `satellite/apps/inspect_goal_evidence.py`,
  `satellite/test_return_path_install_audit.py`, and
  `satellite/test_goal_readiness_inspector.py`,
  `satellite/test_skyseed_session_check.py`,
  `satellite/test_goal_evidence_inspector.py`.
  Live `skyseed_preflight.py --format text` under `seed310` returned
  `ready=True`, with zero scoped containers, networks, direct-backend host
  interfaces, or runtime locks; the only warnings remained
  `host_mpls_not_ready` and `bridge_netfilter_passthrough_not_ready`.
  Live `skyseed_session_check.py --format text` reported branch
  `main...origin/main [ahead 19]`, `dirty_entries=55`,
  `static_evidence_ready=True`, `long_real_run_ready=False`, and
  `next_action=run_host_prepare_then_recheck_preflight_before_real_run`.
  Live `inspect_goal_evidence.py --live-preflight --format text` reported the
  same long-run gate state plus directional isolation fields, and did not start
  a real run.
  A selected-smoke return-path scaling recomputation now reports
  `shared_candidate_zone_routes=18`, with `6` per snapshot, while
  `candidate_zone_route_coverage=0.0` and
  `candidate_zone_route_complete=False` correctly identify that the older
  artifact is using fallback shared-target inference rather than explicit
  planner `candidate_shared_zone_return_route` metadata. Focused tests still
  passed after adding the metadata-only zone-route candidate: `44` tests OK,
  with `py_compile` passing for planner, runtime, return audit, temporal
  inspector/comparator, and the goal/session app entry points.
- `inspect_goal_readiness.py` and `inspect_goal_evidence.py` now surface the
  same candidate zone-route fields in the main goal gates. The current default
  selected runtime scope reports `candidate_zone_routes=12`,
  `candidate_zone_route_coverage=0.0`, and
  `candidate_zone_route_complete=False`, because the selected-smoke artifact is
  still an older fallback-derived artifact. This is the expected baseline: a
  newly generated planner preview with explicit
  `candidate_shared_zone_return_route` metadata should move this coverage
  toward `1.0` without changing current Linux compatibility installation.
- That newly generated preview-only check now exists:
  `goal-1shell-172-preview-zone-route-metadata-smoke`, using planned shell
  `S5`, `172` satellites, `ut3,ut4`, `gw5,gw6,gw7`, `3` slices, and no real
  runtime. Its return-path scaling report has `snapshots=3`, `total_flows=6`,
  `shared_candidate_zone_routes=6`,
  `candidate_zone_route_coverage=1.0`, and
  `candidate_zone_route_complete=True`. The consolidated goal evidence report
  pointed at this run likewise reports
  `candidate_zone_routes=6`,
  `candidate_zone_route_coverage=1.0`, and
  `candidate_zone_route_complete=True`, while still keeping
  `long_real_run_ready=False`. This proves the current planner can emit the
  explicit metadata-only GW->zone return-route boundary without spending a
  full real run or changing current Linux compatibility installation.
- Post-preview verification on the rebooted VM:
  focused return-path/goal tests passed `12` tests OK, and live
  `skyseed_preflight.py --format text` under `seed310` returned `ready=True`
  with zero scoped containers, networks, direct-backend interfaces, or runtime
  locks. The remaining warnings were still only `host_mpls_not_ready` and
  `bridge_netfilter_passthrough_not_ready`, so the long-real-run gate remains
  closed until host prepare is run and preflight is rechecked.
- The return-path migration worklist now separates metadata-boundary readiness
  from actual Linux dataplane migration. On
  `goal-1shell-172-preview-zone-route-metadata-smoke`, the worklist reports
  `candidate_zone_routes=6`, `candidate_zone_route_coverage=1.0`,
  `candidate_zone_route_complete=True`, and `metadata_boundary_ready=True`,
  but still shows current `per_ut_flow_compatibility` install blockers:
  `reverse_lfib_label_stack`, `gateway_ut32_return_route`,
  `gateway_nat_forward_rules`, and
  `serving_satellite_ut32_delivery_route`. Its next backbone gate is therefore
  `replace_current_backbone_install_points_with_shared_zone_return_installation`.
  On the older `goal-1shell-172-preview-return-audit-selected-smoke`, the same
  inspector reports `metadata_boundary_ready=False` and
  `next_gate=complete_planner_candidate_zone_route_and_label_metadata_first`,
  preserving the distinction between old fallback-derived evidence and current
  explicit planner metadata.
- Current implementation has now moved the return-backbone apply path from
  per-flow compatibility into shared zone-return groups. `ZoneReturnInstallIntent`
  carries route prefixes, reverse labels, and reverse path segments; runtime
  apply installs gateway zone-prefix MPLS routes plus shared reverse LFIB/pop
  entries once per `(gateway, zone, responsible satellite)` target. The
  per-flow apply path no longer installs gateway `/32` reverse ingress routes,
  per-flow reverse LFIB entries, or serving-satellite reverse pop entries.
  Serving-satellite UT `/32` delivery routes and gateway edge NAT/FORWARD rules
  remain per-flow/edge-local for now. This is code-level progress only; it has
  passed focused unit/compile checks but has not yet been exercised in a real
  runtime run.

## Iteration 2 Goal

- Improve `1-shell` live temporal runtime timing with focus on steady
  `RuntimeEpoch` and `ParameterEpoch` host-side cost.
- Validate `10s` slicing first, then compare `5s` only if it improves real
  runtime behavior instead of only increasing epoch count.
- Keep temporal fidelity, ping success, rollback correctness, and durable
  environment hygiene while avoiding one-off scale hacks.

## Completed

- Non-escalated `ParameterEpoch` execution now uses the lightweight path and
  stays recovery-free in normal cases.
- Steady host-runtime success-path bookkeeping is now a little tighter:
  - successful host apply no longer re-reads `link_state` just to reconstruct
    the same post-apply target state before verification
  - the manager now updates its cached runtime state directly from the already
    applied `working_state`
  - activation probe now combines per-endpoint netns link/address checks into
    one `nsenter` call instead of two
- The hot `rebind_slot_attachment` command path is also a bit leaner now:
  - host-side `ip link set ... master ...` and `ip link set ... up` are issued
    as one shell step per endpoint instead of two separate subprocess calls
  - netns-side `ip addr flush`, `ip addr add`, and `ip link set ... up` are
    issued as one shell step per endpoint instead of three separate `nsenter`
    calls
  - deactivation now similarly coalesces link-down/address-flush and
    host-side down/nomaster cleanup into single shell steps
- Runtime-parameter mutation policy boundaries are now explicit and replaceable
  in:
  `network_mutation.py`, `temporal_planning.py`,
  `apps/build_starlink_shell_preview.py`, and
  `apps/run_starlink_shell_experiment.py`.
- The runtime link-parameter derivation path is now explicitly modeled as a
  replaceable boundary in `temporal_planning.py` rather than an unnamed
  hard-wired helper chain:
  `DistanceElevationRuntimeParameterModel` is the default behavior, and the
  chosen model name is now carried in snapshot provenance and temporal
  preparation metadata.
- `ParameterEpoch` no-op skipping is now implemented and recorded instead of
  forcing empty host work.
- Real-run monitoring/hygiene is now durable:
  `runtime_progress.json`, `events.jsonl`, stall warnings, scoped preflight,
  runtime lock, and post-run preflight are runner-enforced rather than relying
  on operator memory.
- `timing.json` / `timing.csv` are no longer only end-of-run artifacts:
  live runtime now checkpoints them after each completed epoch and marks them
  with `finalized=false` until the final export succeeds.
- `real_runtime_report.json` and `temporal_audit.json` now follow the same
  checkpoint pattern during live execution, so long runs no longer have to
  finish rollback before any runtime/audit payload exists on disk.
- A durable inspector command now exists for in-progress or partially finalized
  runs:
  `satellite/apps/inspect_temporal_run.py` reads `runtime_progress.json` and
  `events.jsonl` to report whether the live queue is complete, whether
  rollback is the only remaining phase, and what the current lag/wall-time
  stats look like.
- Final artifact export no longer reuses the live `runtime_epoch_result` event
  name for summary-only records, so future `events.jsonl` files stay cleaner
  for lag/timing forensics and run-health inspection.
- Bounded `1-shell` real validations now pass for both dense slice candidates:
  - `1shell-runtime-10s-thresholdfix-w2-bounded1`
  - `1shell-runtime-5s-thresholdfix-w2-noopskip-bounded1`
- The current slice comparison result is now clear enough to guide the next
  run choice:
  - `10s` is the better long-run baseline today because it keeps runtime lag
    lower under the current steady update pace
  - `5s` is technically viable and exercises noop `ParameterEpoch` skipping,
    but it increases queue pressure faster than it shrinks steady update work
  - bounded timing-row evidence from
    `satellite/apps/compare_temporal_runs.py`:
    - `10s` bounded:
      `RuntimeEpoch count=3`, lag average about `5.736s`,
      transition average about `34.841s`,
      host-apply average about `22.610s`;
      steady-only `RuntimeEpoch count=2`,
      transition average about `15.578s`,
      host-apply average about `2.798s`;
      `ParameterEpoch count=3`, lag average about `58.420s`,
      transition average about `3.806s`,
      host-apply average about `0.101s`
    - `5s` bounded:
      `RuntimeEpoch count=4`, lag average about `37.423s`,
      transition average about `30.785s`,
      host-apply average about `18.218s`;
      steady-only `RuntimeEpoch count=3`,
      transition average about `14.135s`,
      host-apply average about `2.324s`;
      `ParameterEpoch count=5`, lag average about `71.970s`,
      transition average about `3.670s`,
      host-apply average about `0.143s`;
      noop `ParameterEpoch count=1`, transition average about `2.593s`
  - Interpretation:
    - `5s` does slightly reduce per-epoch steady `RuntimeEpoch` host-side work
      under bounded conditions
    - but the scheduler queue grows much faster than that per-epoch savings,
      so activation lag is still substantially worse than `10s`
  - Existing bounded run artifacts also show the dominant steady host-side
    mutation family is still `rebind_slot_attachment`, especially on
    cross-plane and access-slot work, while `update_slot_parameters` remains
    comparatively cheap.
  - The newly added timing fields
    `post_apply_state_refresh_wall_seconds` and
    `post_apply_verification_wall_seconds` are now available for the next real
    validation run so we can verify that the latest success-path tightening is
    actually being realized in steady updates.

## In Progress

- Current `1-shell` / `172-sat` G/Z runtime validation is focused on
  distinguishing near-GW ideal behavior from general sparse-shell behavior, and
  on preventing timing regressions while return-path semantics are audited.
- After VM reboot / new session recovery, seed310 preflight was rerun:
  `ready=True`, no scoped Skyseed containers, networks, direct-backend host
  interfaces, or runtime locks were present. The remaining warnings were
  `host_mpls_not_ready` and `bridge_netfilter_passthrough_not_ready`, which are
  expected to be repaired by host prepare before a real-runtime run.
- Preview-only distance matrix artifacts:
  - `goal-1shell-172-preview-distance-near6-catalog`: all 12 UTs covered;
    runtime frames had `0.0` average ISL hops and max `0` ISL hops, matching
    the previously observed real-run `1-hop` behavior when every UT is placed
    near its preferred GW.
  - `goal-1shell-172-preview-distance-mixed6-catalog`: 6 of 12 UTs had valid
    coverage in the selected shell/window; runtime frames averaged `5.381` ISL
    hops with max `20`, so the planner is not hard-coded to force one-hop
    paths.
  - `goal-1shell-172-preview-distance-far12-catalog`: 10 of 12 UTs had valid
    coverage; runtime frames averaged `5.341` ISL hops with max `24`, again
    showing path diversity for farther UT/GW geography.
- Preview update-size evidence from the same matrix:
  - near6 runtime actions stayed at `72`, node subplans at `24`; initial
    network operations were `193`, steady runtime network operations averaged
    `6.0`.
  - mixed6 runtime actions averaged `74.29`, node subplans averaged `33.0`;
    initial network operations were `187`, steady runtime network operations
    averaged `4.83`.
  - far12 runtime actions averaged `112.0`, node subplans averaged `52.89`;
    initial network operations were `191`, steady runtime network operations
    averaged `4.75`.
- Current return path audit confirms the core scale gap remains in
  `linux_mpls_runtime.py`: `DirectGatewayExperimentContext.return_target_prefix`
  is still a UT `/32`; serving satellites install per-UT delivery routes; GWs
  install per-UT reverse ingress routes; reverse LFIB and gateway FORWARD/SNAT
  rules are still derived from per-flow context. The next architectural step is
  to introduce a zone return intent boundary before changing Linux installer
  behavior.
- The first zone-return interface boundary is now explicit in planner output:
  `return_path_intent.intent_kind=zone_return_delivery` says GW return traffic
  should target the active responsible satellite for a zone, followed by
  serving-satellite local UT delivery. The same intent deliberately records
  `current_linux_realization.mode=per_ut_flow_compatibility` so the current
  `/32` reverse-route / per-flow reverse-LFIB implementation is visible as a
  compatibility layer rather than mistaken for the architecture target.
- A short preview-only smoke run,
  `goal-1shell-172-preview-return-intent-smoke`, completed successfully after
  the planner metadata change. Its `planner_preview.json` contains `72`
  `return_path_intent` records and `36` zone service
  `return_delivery_semantics` records.
- Return-path scale auditing is now a durable artifact, not only an ad-hoc
  analysis script:
  - `satellite/return_path_audit.py` computes per-flow compatibility state
    counts versus shared zone/gateway-zone return targets.
  - `satellite/apps/inspect_return_path_scaling.py` reads a run directory or
    `planner_preview.json` and renders text or JSON.
  - `run_starlink_shell_experiment.py` now writes `return_path_audit.json`
    automatically and references it from `prepared_update_bundle.json` and the
    generated-artifact manifest.
  - `satellite/apps/inspect_temporal_run.py` now surfaces a compact
    return-path audit summary when the artifact is present, while older runs
    without the artifact still inspect cleanly with `present=False`.
- Current return-path audit evidence:
  - `goal-1shell-172-preview-return-audit-auto-smoke`: `3` snapshots, `36`
    flows, `36` return intents, per-flow state present, shared target reduction
    available; each snapshot has `12` flows, `6` gateway-zone return targets,
    `12` reverse labels, and `36` gateway NAT/FORWARD compatibility rules.
  - near6 matrix artifact: `156` total flows across `13` snapshots, per-flow
    state present, shared target reduction available; each snapshot has a
    `2.0` flow-to-gateway-zone-target ratio.
  - mixed6 matrix artifact: `78` total flows across `13` snapshots, per-flow
    state present, shared target reduction available; each covered snapshot
    also has a `2.0` flow-to-gateway-zone-target ratio, with more reverse
    labels on longer paths.
  - far12 matrix artifact: `127` total flows across `13` snapshots, per-flow
    state present, but shared target reduction is not available in that sample
    because the catalog is effectively one zone per UT. This keeps the two
    concepts separate: per-flow Linux compatibility remains a scale risk, while
    zone aggregation only reduces backbone state when zones contain multiple
    UTs or flows.
- Timing reporting now surfaces bootstrap, first RuntimeEpoch, and steady
  RuntimeEpoch buckets in the durable inspector/comparator text output, so
  cold initial activation is visible separately from steady dynamic update
  metrics.

- The current long real run is
  `starlink_shell_experiments/1shell-long-600-10s-thresholdfix-w2-watchdog2`.
- Validation command in use:
  `/home/seed/miniconda3/envs/seed310/bin/python satellite/apps/run_starlink_shell_experiment.py --run-mode real-runtime --run-id 1shell-long-600-10s-thresholdfix-w2-watchdog2 --orbit-input-mode planned-shell --planned-shell-id S5 --max-satellites 172 --ut-ids ut3,ut4 --gateway-ids gw5,gw6,gw7 --ground-placement shell-aware --sample-interval 10 --event-evaluation-interval 10 --horizon-seconds 600 --continuous --parameter-max-interval 240 --delay-threshold-ms 0.5 --loss-threshold-pct 0.02 --bandwidth-threshold-pct 5.0 --routing-cost-threshold 5 --runtime-mode fast-demo --loop-count 1 --interval 0 --stream-progress --progress-heartbeat-seconds 15 --startup-stall-warning-seconds 900 --epoch-stall-warning-seconds 300 --preflight-policy require-clean --host-prepare-policy auto --compose-up-timeout-seconds 0 --compose-build-timeout-seconds 0 --container-readiness-timeout-seconds 0 --compose-build-policy if-needed --format json`
- Event evidence from that run already proves the live runtime portion is
  healthy:
  - staged compose startup completed with build reuse
  - `compose_up_wall_seconds=240.318`
  - `container_readiness_wall_seconds=0.126`
  - `initial_runtime_readiness_wall_seconds=2.214`
  - cold host prewarm completed and is correctly separated from steady runtime
    cost:
    - `prewarm_wall_seconds=521.020`
    - `runtime_prepare_wall_seconds=213.589`
    - `runtime_reconcile_wall_seconds=306.988`
  - live runtime executed beyond the initial queue and through replenishment:
    - at least `16` `RuntimeEpoch`s completed
    - at least `31` `ParameterEpoch`s completed
    - `replenished-001` and `replenished-002` were both produced live
  - steady live timings from event records remain in the intended range:
    - `ParameterEpoch` transitions mostly around `3-4s`
    - noop `ParameterEpoch`s around `1.8-2.5s`
    - steady `RuntimeEpoch` transitions around `10-22s`
    - observed steady `RuntimeEpoch` host apply samples around
      `3.89s`, `4.23s`, and `6.89s`
  - activation lag recovers during later replenished execution instead of only
    accumulating:
    - representative later values dropped through about
      `28.7s -> 22.3s -> 14.6s -> 8.6s -> 2.7s`
    - later replenished runtime samples also returned to near-on-schedule
      values such as about `0.31s`, `11.32s`, and `0.32s`
  - no runtime stall warnings were emitted during startup, prewarm, runtime,
    or replenishment
- The same long run is still in slow host rollback while this checkpoint is
  being written, so final `run_manifest.json` / `temporal_audit.json`
  evidence is still pending even though `timing.json`, `timing.csv`, and the
  completed live-runtime event stream are already present.

## Open Questions

- Once the current long `10s` run finishes cleanup, what are the exact final
  averages from `timing.csv` / `timing.json` for:
  - steady `RuntimeEpoch` host apply
  - steady `RuntimeEpoch` total transition
  - live `ParameterEpoch` host apply
  - live `ParameterEpoch` qdisc apply
  - activation lag distributions
- After the `10s` long-flow evidence is complete, is a fresh `5s` long-flow
  attempt still justified, or is bounded evidence already enough to show that
  `5s` increases queue pressure too much for current steady-state pace?

## Risks

- The dominant remaining wall time for long real runs is still outside live
  steady updates:
  - cold host prewarm/bootstrap before the first epoch
  - slow rollback/node-local cleanup after the final epoch
- Because `timing.csv` and `run_manifest.json` are finalized late, a run can
  already have completed the live epoch queue while still appearing
  incomplete from the filesystem.
- If rollback/cleanup code changes, the existing policy of treating teardown as
  a separate timing bucket will need a fresh fully clean validation pass.

## Next Recommended Action

- Let `1shell-long-600-10s-thresholdfix-w2-watchdog2` finish cleanup and then
  extract the final `run_manifest.json`, `temporal_audit.json`, `timing.json`,
  and `timing.csv`.
- Update this checkpoint with exact final averages and audit outcomes.
- If the final `10s` results remain clean, compare against the existing bounded
  `5s` evidence before deciding whether another long `5s` run is worth the
  queue-pressure risk.

## Legacy Baseline Context

- The active system is the Iteration 2 small-scale satellite Internet platform.
- Network construction is still based on SEED/seedemu:
- `apps/build_iteration2_example_topology.py` builds the SEED topology,
- satellite/gateway/UT inventory, and baseline `link_state.json`; shared
- seedemu-facing helpers live in `seed_integration.py`.
- Runtime execution follows the Iteration 2 contract path:
- `TopologySnapshot -> RuntimeUpdatePlan -> NetworkMutationPlan`
- plus node-local Linux/MPLS realization.
- Phase1/controller-driven material is isolated under `legacy_phase1/`.
- Generated outputs, test logs, and Python bytecode caches are not source.

## Active Entry Points

- Build the clean topology:
  `python3 satellite/apps/build_iteration2_example_topology.py`
- Run the Iteration 2 experiment preview or real execution:
  `python3 satellite/apps/iteration2_satellite_experiment.py`
- Run full clean-environment validation:
  `bash satellite/test_bundle_current_network/scripts/run_full_runtime_validation.sh`
- Observe MPLS paths manually:
  `satellite/docs/guides/iteration2_mpls_observation_guide.md`

## Latest Validation

The latest completed full validation was:

- run id: `full_runtime_20260606_223023`
- result: `pass`
- transition count: `5`
- real apply: `success`
- ping: `success`
- rollback: `success`
- average transition wall time: `26.634s`
- dominant measured phase: `network_mutation_plan`
- cleanup: compose stack stopped and no project-labeled containers/networks
  remained

Important observed `ut1` paths:

- `small-scale-slice-0001`: `ut1 -> sat-b1 -> gw2 -> as160-host1`
- `small-scale-slice-0002`: `ut1 -> sat-b1 -> gw2 -> as160-host1`
- `small-scale-slice-0003`: `ut1 -> sat-a2 -> sat-b1 -> gw2 -> as160-host1`
- `small-scale-slice-0004`: `ut1 -> sat-a2 -> gw2 -> as160-host1`
- `small-scale-slice-0005`: `ut1 -> sat-a1 -> sat-a2 -> gw2 -> as160-host1`

## OpenSN-Inspired Host Runtime Optimization Roadmap

This work is now split into two phases and should continue from this single
checkpoint file instead of branching into fragmented status notes.

### Phase A

- performance reporting for end-to-end transition time, `NetworkMutationPlan`
  apply time, host operation timing, node-local apply time, and connectivity
  recovery timing
- explicit host runtime boundary:
  `NetworkMutationPlan -> Host Runtime Envelope -> Host Runtime Manager -> Slot / Link / Namespace / Qdisc Installer`
- in-process Host Runtime Manager owns host-side runtime state for the current
  transition sequence instead of leaving that ownership in the demo harness
- delta-oriented host mutation reporting for skipped, reused, changed,
  created, and deleted slot work where practical
- optional bounded host-side parallelism via `--max-host-workers`, with the
  current controller-backed transport still allowed to force safe serial
  execution

### Phase B

- bypass the Docker network hot path where possible
- replace controller/Docker reconnect churn with direct `netns` / `veth` /
  `qdisc` mutation
- introduce slot/link pools or other precreated reusable resources
- move toward ahead-of-time slice planning plus prepared delta chains

## Current Host Runtime Checkpoint

- Current bottleneck remains host-side `NetworkMutationPlan` execution, not
  planner latency and not node-local MPLS apply.
- The active host mutation path is now:
  `NetworkMutationPlan -> Host Runtime Envelope -> in_process_host_runtime_manager -> direct_netns_veth_slot_link_namespace_qdisc_installer`
- The in-process manager currently owns:
  - cached managed slot state
  - runtime-initialized status
  - last mutation / slice / snapshot identifiers
  - bounded host-worker intent and effective execution decision
- Phase B now bypasses the Docker reconnect hot path during steady-state
  transition execution by mutating direct host `netns` / `veth` / `qdisc`
  resources owned behind the Host Runtime Manager boundary.
- Candidate dynamic links are now precreated and kept inactive while detached
  and addressless until activation so strict runtime semantics still hold.
- Fast-demo continuous real execution remains valid with the direct backend,
  though access-link warmup is now an explicit measured recovery phase.
- Access-link warmup cost turned out to be mostly UT-side neighbor recovery
  after `ut_access` rebinds, not bridge creation or qdisc programming.
- The direct runtime now keeps stable logical MAC addresses on precreated
  candidate endpoint interfaces across rebinds and only warms `ut_access`
  activate/rebind deltas instead of every UT-side parameter update.
- Current clean fast-demo steady-state timings after that fix are:
  - no observe: `2.525s` total, `0.699s` host mutation, `0.079s` access-link warmup
  - one observe selector: `3.998s` total, `0.735s` host mutation, `0.075s` access-link warmup
  - two observe selectors: `5.418s` total, `0.711s` host mutation, `0.095s` access-link warmup
- A watcher-backed no-observe run showed the steady-state sampled UT ping
  recovery gap averaging about `1.650s` with a worst observed gap of
  `1.706s`, so the remaining transition wall time is now larger than the
  visible traffic interruption.

## Current Risks / Cleanup Targets

- `controller.py` still exists for legacy and Phase A compatibility paths, but
  it is no longer on the active steady-state host mutation hot path.
- `mpls_control.py` remains a transitional/legacy MPLS helper used by
  `controller.py` and legacy scripts.
- `build_topology.py` is now an older broad SEED topology builder retained for
  compatibility/reference; the active small-scale app uses `seed_integration.py`
  for shared SEED helpers.
- `legacy_contract_adapters.py` isolates old controller/link-state projection
  helpers from the canonical contract schema module.
- `docs/network_lower_layer_inventory.md` tracks lower-layer Linux/Docker/MPLS
  mechanics that may belong in seedemu or a shared runtime layer later.
- The largest remaining steady-state cost is now connectivity probing and
  validation around the node-local handoff, not access-link warmup itself.

## Next Major Milestone

- After the host-runtime optimization track is stable, the next major
  milestone should be a minimal one-shell Starlink-style run driven by real
  TLE / GP input on top of this non-legacy Iteration 2 path.

## Current 72-Sat Starlink Baseline

- The current bounded Starlink-shell baseline is now `72` satellites on the
  planned-shell `S5` path using the direct backend with dynamic satellite
  links fully off Docker compose networks.
- The hardened `72` compose footprint remains fixed infrastructure only:
  `21` networks total (`6` IX, `11` Mini Internet, `4`
  management/static-ground, `0` dynamic satellite-link) with `105` services
  and `126` service attachments.
- The current supported startup policy is now explicit and scale-aware:
  smaller runs may use simpler bring-up, while `72+` uses staged startup with
  bounded per-stage timeouts plus explicit missing-service retry/recovery for
  satellite batches.
- The final clean cold validation run was
  `starlink_shell_experiments/72-hardened-complete` and passed:
  - `real_apply_success=true`
  - `ping_success=true`
  - `rollback_completed=true`
  - `no_future_active_link_violation=true`
  - `no_future_forwarding_state_violation=true`
  - post-run preflight `ready=True` with no scoped residue
- Cold staged startup is now explicitly supported through recovery rather than
  treated as a hidden flake. In the clean validated run,
  `satellites-06` used `single_service_retry` recovery and finished with no
  missing services.
- Cold startup/readiness timing from that validated run:
  - compose build: `10.498s`
  - compose up: `161.543s`
  - container readiness: `0.052s`
  - initial runtime readiness: `0.738s`
  - pre-epoch host prewarm: `187.237s`
  - pre-epoch host prepare: `137.346s`
  - pre-epoch host reconcile: `49.889s`
- Runtime timing from the same validated run:
  - first runtime epoch transition: `22.664s`
  - steady runtime epoch transition: `6.435s`
  - steady runtime host apply: `3.802s`
  - steady runtime reconcile: `0.000091s`
  - parameter epoch average transition: `3.865s`
  - parameter epoch average dirty reconcile: `0.001s`
  - parameter epoch average qdisc apply (summed per-op timing): `10.506s`
- Parameter epochs remain lightweight and recovery-free in the current
  baseline:
  - `parameter_refresh_recovery_used=false`
  - `topology_mutation_count=0`
  - `forwarding_mutation_count=0`
- The durable hygiene path is now part of the baseline too:
  `satellite/apps/skyseed_preflight.py`, the known-issues runbook, and
  `AGENTS.md` together define the preflight-before-real-run rule and the
  “inspect artifacts before rerunning” rule for failed `72+` startup/runtime
  attempts.
- Real Starlink-shell runs now also have a runner-enforced hygiene lifecycle:
  host prepare, scoped preflight, active runtime lock, failure-safe scoped
  compose cleanup, and post-run preflight are recorded as run artifacts rather
  than relying on operator memory after VM reboot or disconnect.
- Staged compose startup now has an explicit boundary between:
  - overall startup deadline
  - per-stage watchdog/recovery timeout
  Disabling or exhausting the overall startup deadline no longer disables
  per-stage recovery, which was a real one-shell startup regression observed
  during long event-time validation.
- The remaining dominant cost before one-shell work is no longer compose
  network shape or parameter-epoch recovery. It is cold bootstrap/prewarm
  cost, with any future optimization effort best focused there rather than on
  broad architecture changes or `172` real runtime.

## Minimal Verification After Code Changes

Use these checks after ordinary code cleanup:

```bash
python3 -m py_compile \
  satellite/apps/build_iteration2_example_topology.py \
  satellite/apps/iteration2_satellite_experiment.py \
  satellite/contracts.py \
  satellite/legacy_contract_adapters.py \
  satellite/seed_integration.py \
  satellite/topology_pipeline.py \
  satellite/experiment_topology.py \
  satellite/planner.py \
  satellite/planner_validation.py \
  satellite/runtime_projection.py \
  satellite/agent_envelope.py \
  satellite/distribution_store.py \
  satellite/node_local_agent.py \
  satellite/installer_runtime.py \
  satellite/network_mutation.py \
  satellite/network_runtime.py \
  satellite/linux_mpls_runtime.py
```

Use the full validation after runtime/MPLS/topology changes:

```bash
bash satellite/test_bundle_current_network/scripts/run_full_runtime_validation.sh
```
