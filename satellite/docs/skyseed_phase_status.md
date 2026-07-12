# Skyseed Phase Status

Updated: 2026-07-11.

## Current Phase

Shared G/Z v2 architecture is implemented and offline-gated. A real 4H run is
not authorized by this round.

## Last Proven Facts

- The immutable-policy/shared-FEC architecture passes the complete r134 frozen
  corpus: 100/100 current-code replans and compiles, H0-H3 each 25/25, including
  physical generation zero, with no blocker or map conflict.
- The offline companion gate passes 26/26 and full unit discovery passes
  778/778. The canonical profile dry-run is continuous 4H, uncapped, and keeps
  the runtime observer nonblocking at 30-second intervals.
- No canonical real-runtime or long run followed the shared G/Z offline gate;
  bounded companion fixtures completed scoped cleanup.

- r121 completed compose, physical prewarm/current install, and all 25 H0 BPF
  generation loads, then completed 10 H0 epochs before failing closed at epoch
  11. The strict miss samples were host Avahi mDNS from experiment bridge
  gateway addresses, not a missing generation/map entry or Linux fallback.
- r121 also proved a failure-teardown race: the H1 process worker completed a
  91-resource delta after cleanup had selected the parent's 681-resource owned
  scope. Runtime rollback correctly failed with 91 interfaces remaining;
  compose teardown then restored a clean post-run environment.
- The r122 gate makes prepared bridge/veth links unicast-only, starts workers in
  owned process groups, quiesces them before teardown, and merges only their
  frozen delta owned scope into bounded cleanup. Full unit discovery is 744/744
  and companion schema v5 is 15/15 with exact r121 artifact replay.
- r122 was then stopped before runtime when live H0 prewarm inspection found the
  direct veths fixed but compose-created GW IX bridges/endpoints still
  multicast-capable. r123 freezes deterministic names for the 12 compose IX
  bridges and prepares both exact bridges and frozen PID/netns endpoint refs as
  unicast-only before BPF attach.
- Replay determinism is fixed for the pinned replay path. Candidate replay now
  pins `source_timestamp_utc` / coordinate epoch from the baseline manifest and
  pins the selected epoch queue from the baseline `prepared_update_bundle` in
  replay mode. Gate-only run
  `full-s4-bpf-owned-replay-pinned-20260623-gate-only-r2` passed with
  `mismatch_count=0`, no baseline refresh, and
  `source_timestamp_utc=2026-06-23T00:48:17Z`.
- Shortened full-S4 strict `bpf_owned` optimization regression: PASS in
  `satellite/output/starlink_shell_experiments/full-s4-bpf-owned-strict-opt-regression-20260623-pinned-r1`.
  Replay/equivalence passed first with `mismatch_count=0` and
  `runtime_conclusion_allowed=true`. Runtime completed with
  `aggregate_runtime_succeeded=true`, `event_timeline_execution_success=true`,
  `rollback_success=true`, `post_run_preflight_clean=true`, final connectivity
  success, final validation BPF miss/drop delta `0/0`, and run-level
  `bpf_classifier_miss_count=0`.
- Strict `bpf_owned` separation remained intact in the shortened optimization
  regression: `linux_gz_fallback_command_count=0`,
  `eligible_core_fallback_to_linux=0`, and no executed legacy Linux
  G/Z/MPLS/LFIB normal-path apply/probe/verify appeared in eligible
  `bpf_owned` scope. Runtime MPLS route contract records were skipped with
  strict no-fallback attribution; host MPLS route reads were preflight/post-run
  residue checks, not normal-path forwarding.
- BPF forwarding was exercised in the shortened optimization regression:
  final validation delta had `classifier_hit=68`, `label_push=68`,
  `core_lfib_hit=32`, `label_swap=32`, `egress_handoff_hit=68`, and
  `label_pop=68`. Run-level counters included `bpf_ping_path_hit_count=223`
  and `bpf_return_handoff_hit_count=106`.
- Cleanup for `full-s4-bpf-owned-strict-opt-regression-20260623-pinned-r1` used
  the same generated compose file and project as startup:
  `docker-compose -f .../full-s4-bpf-owned-strict-opt-regression-20260623-pinned-r1/seed_output/docker-compose.yml -p seed_output down --remove-orphans -v`.
  The cleanup returned `0`; post-run preflight was clean with compose project
  containers `0`, compose project networks `0`, direct backend host interfaces
  `0`, BPF pins `0`, and runtime lock absent.
- Full-S4 bounded strict BPF/EventTimeline runtime bring-up: PASS in
  `satellite/output/starlink_shell_experiments/full-s4-bounded-bpf-retry-20260623-zone-delivery-rewrite-epoch4`.
  The run completed `12/12` RuntimeTransactions and `1/1`
  ParameterUpdateBatches with `aggregate_runtime_succeeded=True` and
  `event_timeline_execution_success=True`.
- Strict BPF-owned no Linux G/Z/MPLS/LFIB fallback remained intact in that run:
  `linux_gz_fallback_enabled=False`, `linux_gz_fallback_command_count=0`,
  `eligible_core_fallback_to_linux=0`, `backend_apply_fallback_to_linux=0`,
  and final BPF miss/drop was `0/0`.
- BPF forwarding was exercised, not bypassed: `core_lfib_hit=32`,
  `label_swap=32`, `bpf_ping_path_hit_count=223`, and
  `bpf_return_handoff_hit_count=106`.
- Rollback and post-run cleanup were clean. `satellite/output` remains
  untracked/unstaged.
- The tc flower local-IP bypass is scoped to classifier-interface local
  control-plane/IX diagnostic IPv4 addresses, installed as `protocol ip`
  rules ahead of BPF. It is not a Linux G/Z/MPLS/LFIB fallback and does not
  bypass strict G/Z classifier misses or MPLS shim LFIB/handoff misses.
- UT-specific reverse labels are limited to zone-local final delivery. Core
  G/Z FEC identity remains zone/class/anchor based, and core LFIB lookup stays
  label/generation/interface based without packet-keyed destination IP routes.
- Strict BPF-owned no Linux fallback: earlier passing minimal/subset artifact
  is `/tmp/skyseed_event_refinement_bounded_real/event-timeline-refined-bpf-owned-strict-netns-attach-v4`.
- MPLS-like tc-BPF live smoke: PASS, documented in
  `satellite/docs/skyseed_bpf_owned_mpls_like_dataplane.md`.
- Minimal multi-hop MPLS-like live smoke: PASS for the bounded netns smoke
  described in `satellite/docs/skyseed_bpf_owned_mpls_like_dataplane.md`.
- Default plus override flow with generation switch: PASS in focused simulator
  and loader tests, including `satellite/test_bpf_mpls_dataplane.py` and
  `satellite/test_bpf_mpls_live.py`.
- S4 coordinate-consistent workload preview: PASS, with summary artifact
  `satellite/baselines/skyseed_coordinate_consistent_s4_preview_summary.json`.
- Full S4 ground endpoint/container startup readiness: PASS up to
  `runtime_started` in the older
  `/tmp/skyseed_s4_endpoint_fix_runtime3_20260622/full-s4-348-endpoint-fix-runtime3-20260622/events.jsonl`.
  Runtime evidence is superseded by the bounded PASS artifact above.

## Current Blockers

- Canonical 4H acceptance remains blocked until r122 completes 100/100 runtime
  epochs across H0-H3 with H1-H3 process maintenance, zero miss/fallback and
  forbidden work, bounded successful rollback, and a clean post-run preflight.
- No current correctness blocker for bounded full-S4 strict BPF/EventTimeline
  bring-up or the shortened full-S4 strict `bpf_owned` optimization
  regression.
- Remaining caveats: long-run acceptance is not done; final performance
  acceptance is not done; not S5 acceptance; final large-scale S5 acceptance is
  not done; richer 2-hop/3+ multi-hop stress coverage still needs a later
  workload/window if path coverage remains shallow.
- The shortened optimization regression had pre-existing cumulative BPF map
  miss/drop `4/4` before final validation. It is accepted only because the
  final validation delta and run-level miss gate were `0/0`; long-run
  acceptance must add a runtime-start counter baseline or explicit counter
  reset/accounting.
- Full BPF attach reuse is not yet achieved. The shortened optimization
  regression had two full attach-required epochs
  (`no_previous_bpf_owned_readiness`, `interface_role_set_mismatch`) and one
  delta attach epoch (`delta_attach_count=16`, `reused_attach_count=46`).
- Performance/prewarm/reconcile cost remains high. The epoch4 cost attribution
  records `prewarm_wall_seconds=2707.154934`,
  `reconcile_wall_seconds=2374.42018`,
  `qdisc_delete=1254.161113` seconds for `31900` operations,
  `shell_batch=1100.135287` seconds for `33176` operations,
  `veth operations=232.490254` seconds for `7669` operations,
  `compose_up_wall_seconds=374.129518`, and
  `compose_down_after_wall_seconds=113.44729`. The run wall window was
  approximately 4929 seconds from `2026-06-23T04:37:44Z` to
  `2026-06-23T05:59:53Z`.
- Future long-run measurements should use the documented
  `operational_clean` profile design in
  `satellite/docs/skyseed_operational_clean_long_run_profile.md`; it is not
  executed or accepted yet.

## Do Not Redo

- old S5 A/B;
- packet-keyed per-hop IP-route shortcut;
- 300s sampling evidence as optimization;
- Linux fallback for strict BPF.

## Next Gate

Next engineering gate: design and implement an operational-clean long-run path
without weakening strict BPF semantics. Required work includes runtime-start
BPF counter baselining or explicit counter reset/accounting, lightweight
heartbeat and first-failure diagnostics, no correctness-heavy full validation
every epoch, qdisc no-op detection, avoiding full qdisc delete/recreate where
safe, reducing docker exec / ip / tc command count, batching or in-node agent
execution, diff-based reconcile, safe limited parallelism for read-only
discovery, and careful serialization for RTNL/netlink-mutating operations.

Any next gate must remain strict: no Linux G/Z/MPLS/LFIB fallback for eligible
BPF-owned scope, no packet-keyed IP route fallback, no semantic weakening, no
`satellite/output` commit, and no push.

## Notes On Superseded Evidence

- `satellite/docs/skyseed_pre_s4_final_readiness.md` is a historical blocked
  audit. Its S4 preview blocker is superseded by
  `satellite/docs/skyseed_final_consolidated_pre_s4_gate.md` and
  `satellite/baselines/skyseed_coordinate_consistent_s4_preview_summary.json`.
- `/tmp/skyseed_mpls_bpf_owned_multihop_runtime/mpls-bpf-owned-multihop-runtime-20260622`
  is not a full runtime PASS artifact: it shows strict BPF/MPLS-like apply and
  EventTimeline execution, but final connectivity failed. Treat it as useful
  partial evidence, not as the next-gate success result.
