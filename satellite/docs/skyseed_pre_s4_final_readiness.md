# Skyseed Pre-S4 Final Readiness Audit

Date: 2026-06-21

Scope: final readiness gate before the first S4 / about 348-satellite bounded
real runtime. This audit did not run S4 real runtime, did not run a long S5 A/B,
did not change G/Z, FEC, or MPLS-like semantics, did not add Linux fallback to
strict `bpf_owned`, did not implement qdisc/TheaterQ, did not commit
`satellite/output`, and did not push.

## Decision

Overall S4 bounded-run readiness: BLOCKED.

Primary blockers:

- The refreshed full-catalog S4 preview at
  `/tmp/skyseed_pre_s4_preview_final_full` selected 348 satellites, 32 UTs,
  12 gateways, and 16 zones, but all six required service zones were unresolved
  for all 13 physical samples. It produced `expected_bpf_owned_scope_count=0`,
  `expected_linux_mpls_lfib_elision_count=0`, and final G/Z FEC count `0`.
- The latest successful strict BPF artifact proves netns attaches, packet-route
  ifindices, no self-redirect routes, global BPF redirect hits, and global
  return-handoff hits, but the successful-path artifact does not expose
  per-interface BPF seen/redirect counter maps. Those counters are implemented
  in the failed-path diagnostic helper and covered by unit tests, but this gate
  required them in the latest bounded artifact.

## Readiness Matrix

| Gate | Decision |
| --- | --- |
| `bpf_owned_strict_ready` | BLOCKED |
| `event_timeline_runtime_ready` | PASS |
| `refined_event_time_ready` | PASS |
| `sampling_update_model_ready` | PASS |
| `qdisc_lifecycle_honesty_ready` | PASS |
| `s4_preview_ready` | BLOCKED |
| `artifact_hygiene_ready` | PASS |
| `overall_s4_bounded_run_ready` | BLOCKED |

## Strict BPF-Owned Gate

Latest bounded artifact:
`/tmp/skyseed_event_refinement_bounded_real/event-timeline-refined-bpf-owned-strict-netns-attach-v4`.

Passing evidence from `run_manifest.json`, `real_runtime_report.json`, and
`temporal_audit.json`:

- `linux_gz_fallback_enabled=false`
- `linux_gz_fallback_command_count=0`
- `eligible_core_fallback_to_linux=0`
- `skipped_by_bpf_backend_count=18` on runtime apply records
- `bpf_classifier_miss_count=0`
- `bpf_ping_path_hit_count=72`
- `bpf_return_handoff_hit_count=24`
- `aggregate_runtime_succeeded=true`
- `rollback_success=true`, `rollback_completed=true`
- `post_run_preflight_clean=true`

Mode boundary from code and tests:

- `linux_mpls` remains canonical Linux MPLS/G/Z apply.
- `bpf_shadow` and `bpf_canary` retain Linux apply and do not skip Linux
  forwarding.
- `bpf_owned` skips only proven eligible core scopes:
  `satellite_core_mpls_lfib` and `mpls_readiness_sysctl`.
- Excluded scopes remain Linux/host owned: qdisc delay/rate/loss/bandwidth,
  veth/netns/address lifecycle, UT/GW edge IP behavior, iptables/SNAT,
  Mini Internet edge routes, and fallback/control traffic.
- Serving-satellite source rule and serving-satellite policy-table verify are
  included only when classified as G/Z/MPLS-like core compatibility checks.
  UT default route, unrelated edge policy, Mini Internet edge route, iptables,
  SNAT, non-G/Z control traffic, and broad policy-routing verification are
  excluded.

Generalization evidence:

- Packet routes are derived from `RuntimeUpdatePlan` through G/Z FEC and
  UT/GW/zone flow context.
- Generated directions include `g_to_z_access_ingress`, `z_to_g`, and
  `z_to_g_nat_ingress` in the latest single-hop bounded artifact.
- Deterministic tests use arbitrary `ut42` / `gw9` selectors for path
  observability and guard against one-off `ut3` / `gw5` behavior.

Attach/path evidence:

- Attach targets are `netns:<container>:<iface>` and attach commands use
  `nsenter ... tc ...`, not only root-netns host peers.
- The latest artifact has 8 attached netns interfaces and 32 resolved packet
  route entries.
- Resolved packet routes have `ingress_ifindex` and `egress_ifindex`.
- Self-redirect check over the latest artifact found `self_redirect_count=0`.
- Global BPF counters prove the ping and return/handoff paths were hit:
  redirect hits `72`, return handoff hits `24`, classifier misses `0`.
- BLOCKED sub-evidence: successful-path artifacts do not expose
  `per_interface_bpf_seen_count` / `per_interface_redirect_count`; those are
  emitted by the packet-path diagnostics helper only on failed selectors.

## EventTimeline Runtime

The real runtime is not preview-only.

- Queue entries in `epoch_execution_queue.json` carry `source=event_timeline`.
- Queue entries preserve executor-compatible `epoch_type` values:
  `runtime_epoch` and `parameter_epoch`.
- Queue entries expose `event_timeline_type` values:
  `runtime_transaction` and `parameter_update_batch`.
- `temporal_audit.json` reports:
  `bpf_owned_backend_consumes_event_timeline_transactions=true` and
  `qdisc_updates_execute_only_from_parameter_update_batches=true`.
- Executed counts match selected queue counts:
  `executed_runtime_transaction_count=4` and
  `executed_parameter_update_batch_count=1`.

Artifact count evidence:

- `physical_sample_count=13`
- `runtime_transaction_count=4`
- `parameter_update_batch_count=1`
- `executed_runtime_transaction_count=4`
- `executed_parameter_update_batch_count=1`
- `sample_aligned_event_count=9`
- `refined_event_count=4`

## Refined Event-Time Level 3

Level 3 refined event-time: PASS.

Evidence:

- Bounded local bisection is implemented with bracket, iteration, and precision
  metadata.
- Latest runtime queue includes non-sample-aligned refined runtime transactions:
  `30s -> 25s`, `50s -> 45s`, `70s -> 65s`, and `80s -> 75s`.
- Latest artifact has `refinement_method=bounded_local_bisection`,
  `refinement_iteration_count=4`, and search bracket metadata.
- Tests cover coverage invalidation, UT handover, gateway selection change, and
  structural active/inactive refinement.
- Latest artifact includes a combined event at `65s` with UT serving-satellite
  change, zone anchor change, gateway selection change, G/Z path change, BPF
  generation content change, and structural link change.
- Coverage invalidation and active/inactive transitions force
  RuntimeTransactions; they are not suppressible by dwell/hysteresis in the
  tested model.

Level 4 continuous parameter timeline: NOT IMPLEMENTED by design.
`ParameterUpdateBatch` remains threshold-based, and qdisc updates remain
discrete.

## Sampling And Update Model

Sampling/update model: PASS.

- 10s scan remains the canonical preview reference.
- 300s preview remains coarse preview only, not optimization evidence.
- Physical samples do not automatically become RuntimeTransactions.
- Metadata-only and timestamp-only changes do not trigger RuntimeTransactions.
- Cost-only drift is ignored unless a parameter policy explicitly allows it.
- RuntimeTransaction triggers are semantic: FEC path content, G->Z/Z->G path,
  UT serving satellite, zone anchor, gateway selection, structural
  active/inactive, and BPF generation content changes.
- ParameterUpdateBatch triggers are threshold/state based: delay, bandwidth or
  rate, loss/drop, active/inactive, and drop-state.

## qdisc And Lifecycle Honesty

qdisc/lifecycle honesty: PASS.

Not claimed as optimized:

- qdisc delay/rate/loss/bandwidth
- veth/netns/address lifecycle
- ordinary lifecycle shell batches
- full continuous parameter function
- TheaterQ/custom qdisc
- large node-local agent redesign

qdisc remains Linux-owned. `ParameterUpdateBatch` is discrete and
threshold-based.

## S4 Preview

Refreshed command class: preview-only S4, planned shell S4, 348 satellites,
10s sample/evaluation, 120s horizon, 32 UT catalog, 12 gateway catalog, 16 zone
catalog, `/tmp` output, no SEED render, no real runtime.

Output: `/tmp/skyseed_pre_s4_preview_final_full`.

Required preview fields:

- satellite count: `348`
- UT count: `32`
- GW count: `12`
- zone count: `16`
- active zone count: `10`
- physical sample count: `13`
- RuntimeTransaction count: `10`
- ParameterUpdateBatch count: `3`
- refined event count: `9`
- sample-aligned event count: `4`
- expected BPF generation count: `10`
- expected BPF-owned scope count: `0`
- expected Linux MPLS/LFIB elision count: `0`
- expected qdisc update count: `37`
- expected qdisc command count: `37`
- expected lifecycle/link operation count: `386`
- expected lifecycle plus parameter operation count: `531`
- G/Z FEC count: `0`
- G->Z / Z->G path counts: `0` / `0`
- UT serving-satellite change count: `0`
- zone anchor change count: `6`
- GW selection change count: `0`
- structural link active/inactive count: `6`
- coverage invalidation forced change count: `0`

Coverage/risky-zone summary:

- All required service zones are unresolved for all 13 samples.
- Required unresolved zones: `zone-east-asia-pacific-wide`,
  `zone-europe-west-wide`, `zone-middle-east-south-asia-wide`,
  `zone-north-america-east-wide`, `zone-north-america-west-wide`,
  `zone-south-america-wide`.
- Optional active zones are also unresolved:
  `zone-africa-east-optional`, `zone-africa-west-optional`,
  `zone-australia-east-optional`, `zone-central-asia-optional`.
- S4 coverage is not sufficient for the requested full catalog preview.
  S3/720 fallback or catalog/placement correction should be considered before
  any S4 real runtime.

S4 preview readiness: BLOCKED.

## Hygiene

Worktree status at audit time includes pre-existing modified and untracked
files. This audit added only:

- `satellite/docs/skyseed_pre_s4_final_readiness.md`
- `satellite/baselines/skyseed_pre_s4_final_readiness_summary.json`

Hygiene checks:

- `git status --short`: dirty worktree, listed in the JSON summary.
- No tracked or staged `satellite/output` changes.
- Active runtime lock absent:
  `satellite/output/skyseed_real_runtime_active.lock` does not exist.
- `/tmp` runtime artifacts are not staged.

Modified files by category:

- BPF dataplane: `satellite/bpf_backend.py`,
  `satellite/bpf_dataplane_canary.py`,
  `satellite/experimental/bpf/skyseed_tc_dataplane_canary.c`,
  `satellite/test_bpf_shadow_backend.py`.
- Runtime backend: `satellite/linux_mpls_runtime.py`,
  `satellite/runtime_operation_metrics.py`,
  `satellite/test_rollback_cleanup_optimization.py`.
- Event timeline planner: `satellite/temporal_planning.py`,
  `satellite/test_event_timeline_runtime_queue.py`.
- Preview/workload: `satellite/apps/build_starlink_shell_preview.py`,
  `satellite/apps/run_starlink_shell_experiment.py`,
  `satellite/experiment_topology.py`,
  `satellite/test_sampling_update_optimization.py`.
- Tests: `satellite/test_event_timeline_runtime_queue.py`,
  `satellite/test_sampling_update_optimization.py`,
  `satellite/test_bpf_shadow_backend.py`,
  `satellite/test_rollback_cleanup_optimization.py`.
- Docs/baselines: existing docs/baselines plus this audit doc and summary.

## Validation

Commands run:

```bash
python -m py_compile satellite/linux_mpls_runtime.py satellite/bpf_dataplane_canary.py satellite/bpf_backend.py satellite/temporal_planning.py satellite/apps/run_starlink_shell_experiment.py satellite/apps/build_starlink_shell_preview.py satellite/test_rollback_cleanup_optimization.py satellite/test_bpf_shadow_backend.py satellite/test_event_timeline_runtime_queue.py satellite/test_sampling_update_optimization.py satellite/test_gz_fec_bpf_generation_compiler.py

python -m unittest satellite.test_rollback_cleanup_optimization satellite.test_bpf_shadow_backend satellite.test_event_timeline_runtime_queue satellite.test_sampling_update_optimization satellite.test_gz_fec_bpf_generation_compiler

git diff --check
```

Results:

- py_compile: PASS
- focused unit tests: PASS, `Ran 52 tests`, `OK`
- `git diff --check`: PASS

