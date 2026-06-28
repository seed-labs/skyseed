# Skyseed Runtime Bottleneck Measurement Hardening

Date: 2026-06-21

Scope: measurement hardening only. This does not implement BPF-owned
replacement, qdisc/TheaterQ optimization, or G/Z semantic changes. No long S5
A/B or staged acceptance was run.

Machine-readable summary:
`satellite/baselines/skyseed_runtime_bottleneck_measurement_hardening_summary.json`

## What Changed

The node-local runtime path now records and reports measurement fields that
separate count, cumulative wall, and critical-path semantics more clearly.

Added instrumentation:

- `NodeLocalBatchResult.command_timings`: new runs record per-command marker
  timings from inside the node-local batch shell.
- `summarize_node_local_batches(...)`: now reports command counts by semantic
  scope and cleanup scope.
- node-local typed metrics now include:
  - `cumulative_batch_wall_seconds`
  - `max_batch_wall_seconds`
  - `worker_critical_path_wall_seconds_source`
  - `per_command_timing_available`
  - `per_command_timing_count`
  - `counts_by_scope`
  - `cleanup_counts_by_scope`
  - `planned_state_touch_count_by_scope`
  - `wall_seconds_by_scope` when per-command timing is available
  - `bpf_owned_gz_forwarding_candidate`
- rollback cleanup metrics now include cleanup scope counts and the count of
  cleanup commands that would shrink if G/Z forwarding became BPF-owned.

The worker critical path remains the caller's phase timing, for example
`node_local_apply_wall_seconds` or `rollback_wall_seconds`. Batch wall and command
wall are cumulative worker metrics, not critical-path wall.

## Scope Classifier

Node-local apply/cleanup commands are classified into:

- `satellite_core_mpls_lfib`: G/Z/MPLS-like forwarding route/LFIB work,
  including forward and shared/reverse compatibility route installs.
- `ip_route_rule`: source rules, UT default routes, UT delivery routes, and
  related route/rule state.
- `edge_nat_iptables`: gateway FORWARD/SNAT rules.
- `mpls_readiness_sysctl`: platform-label and MPLS input readiness.
- `fallback_control_route`: generic/control route operations.
- `other_unclassified`.

Rollback cleanup maps the same commands into residue scopes, for example
`satellite_core_mpls_lfib_residue` and `edge_iptables_snat_residue`.

## Existing Evidence Reclassified

Using the existing Goal 20 S5 BPF-off 3600s run:

`satellite/output/starlink_shell_experiments/goal20-s5-bpf-ab-a-off-3600-20260621-r2`

Steady RuntimeEpoch count: 154.

Phase wall, direct evidence:

| Phase | Sum | Avg | p50 | p95 | Share of Runtime wall sum |
| --- | ---: | ---: | ---: | ---: | ---: |
| Host/direct apply | 483.444s | 3.139s | 2.695s | 5.924s | 42.7% |
| Node-local apply | 193.584s | 1.257s | 1.181s | 2.490s | 17.1% |
| Node-local incremental cleanup | 363.856s | 2.363s | 2.137s | 5.094s | 32.1% |

Node-local apply command counts by hardened scope:

| Scope | Count |
| --- | ---: |
| satellite-core MPLS/LFIB | 1118 |
| ip route / ip rule | 1260 |
| edge NAT / iptables | 1260 |
| MPLS readiness sysctl | 942 |

Node-local incremental cleanup command counts by hardened cleanup scope:

| Cleanup scope | Count |
| --- | ---: |
| satellite-core MPLS/LFIB residue | 974 |
| ip route / ip rule residue | 1260 |
| edge iptables/SNAT residue | 1260 |
| MPLS readiness/sysctl cleanup or checks | 942 |

Host/direct cumulative command wall, direct cumulative evidence:

| Family | Cumulative wall |
| --- | ---: |
| qdisc | 370.765s |
| shell_batch | 286.024s |
| veth | 136.123s |
| ip | 98.842s |

These host command-wall values are cumulative and not critical-path wall.

## Cleanup And BPF Ownership

The reclassified existing artifacts show that cleanup is partly caused by the
same Linux route/MPLS/rule/iptables state that node-local apply installs.
Therefore cleanup should not be treated as unrelated to forwarding replacement.

If only satellite-core G/Z forwarding became `bpf_owned`, the most direct cleanup
scope expected to shrink is:

- `satellite_core_mpls_lfib_residue`: 974 cleanup commands in the existing A run.

Scopes that would not automatically shrink without broader ownership:

- `edge_iptables_snat_residue`
- `ip_route_rule_residue`
- `mpls_readiness_sysctl_cleanup_or_check`
- host/direct qdisc
- veth/link/address lifecycle

## Evidence Labels

Direct evidence:

- RuntimeEpoch phase wall for host apply, node-local apply, and node-local
  cleanup.
- Host/direct cumulative command wall by family.
- Node-local command counts by scope.
- New bounded runs will provide node-local per-command wall via
  `command_timings`.

Cumulative but not critical-path evidence:

- host `qdisc`, `shell_batch`, `veth`, `ip`, `link`, and `addr` wall sums.
- node-local `batch_wall_seconds` and `cumulative_batch_wall_seconds`.

Batch-level inference:

- Existing Goal 20 node-local apply/cleanup scope wall. Old artifacts did not
  contain per-command timings, so scope-specific wall cannot be recovered.

Unknown until targeted run:

- node-local per-scope wall for apply and cleanup.
- host `shell_batch` subcommand wall split.
- planner/FEC diff subphase wall.

## Optimization Candidates

BPF-owned satellite-core MPLS replacement:

- Evidence-supported for command and cleanup scope reduction.
- Existing A run has 1118 apply commands and 974 incremental cleanup commands in
  satellite-core MPLS/LFIB scopes.
- Wall-time benefit needs a new bounded run with per-command `command_timings`.

MPLS readiness sysctl cache / one-time setup:

- Evidence-supported candidate.
- Existing A run has 942 apply commands and 942 incremental cleanup/check commands
  in readiness scope.
- Must preserve rollback and readiness semantics.

Cleanup scope narrowing:

- Evidence-supported candidate.
- Cleanup has identifiable forwarding, edge, route/rule, and readiness scopes.

qdisc unchanged-parameter elision:

- Evidence-supported candidate from host/direct cumulative command wall.
- Not addressed by BPF-owned MPLS replacement.

shell_batch/lifecycle optimization:

- Evidence-supported candidate from cumulative host command wall.
- Needs subcommand timing before picking exact lifecycle/address/link targets.

## Validation

Instrumentation was validated with unit tests only:

`python3 -m unittest satellite/test_rollback_cleanup_optimization.py`

No runtime output was committed.
