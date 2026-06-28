# Skyseed Runtime Bottleneck Recheck

Date: 2026-06-21

Scope: evidence recheck before BPF-owned MPLS replacement. This pass used
existing Goal 20 S5 3600s artifacts and did not run long S5 A/B, staged
acceptance, or BPF replacement work.

Machine-readable compact summary:
`satellite/baselines/skyseed_runtime_bottleneck_recheck_summary.json`

## Evidence Base

Primary current-path evidence:

- A, BPF off:
  `satellite/output/starlink_shell_experiments/goal20-s5-bpf-ab-a-off-3600-20260621-r2`
- Steady RuntimeEpoch samples: 154, excluding the first materialization epoch.
- ParameterEpoch samples: 145.

Cross-check:

- B, BPF full-scope overlay:
  `satellite/output/starlink_shell_experiments/goal20-s5-bpf-ab-b-fullscope-3600-20260621`
- B has the same canonical host/node-local command surface, so it confirms
  stability of the attribution but is not replacement evidence.

## RuntimeEpoch Wall Time

Steady RuntimeEpoch, A run:

| Component | Sum | Avg | p50 | p95 | Max | Share of Runtime wall sum |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Total RuntimeEpoch wall | 1132.863s | 7.356s | 6.612s | 14.189s | 26.199s | 100.0% |
| NetworkMutationPlan host apply | 483.444s | 3.139s | 2.695s | 5.924s | 13.545s | 42.7% |
| Node-local Linux/MPLS apply | 193.584s | 1.257s | 1.181s | 2.490s | 3.912s | 17.1% |
| Node-local incremental cleanup | 363.856s | 2.363s | 2.137s | 5.094s | 8.145s | 32.1% |
| Runtime unclassified residual | 91.924s | 0.597s | 0.538s | 1.044s | 1.666s | 8.1% |
| Post-apply validation probe | 0.001s | 0.000007s | near zero | near zero | 0.000269s | ~0% |
| Artifact/audit blocking write | 0.000s | ~0.000001s | near zero | near zero | near zero | ~0% |

The component shares above should be read as attribution estimates from existing
semantic timing fields, not a perfect additive flame graph. The conclusion is
stable anyway: host/direct apply and cleanup dominate steady RuntimeEpoch wall;
node-local Linux/MPLS apply is material but smaller.

Planner / FEC diff / artifact preparation:

- Startup preview/artifact prep is visible as a one-time `preview_wall_seconds`
  of 14.251s.
- Per RuntimeEpoch planner/FEC diff is not separately instrumented. The closest
  existing field is `runtime_unclassified_wall_seconds`: avg 0.597s, p95
  1.044s, max 1.666s.
- Label: unknown / needs targeted run if exact planner/FEC diff split matters.

## Host Apply Attribution

Steady RuntimeEpoch host/direct apply, A run:

| Family | Command count | Wall |
| --- | ---: | ---: |
| qdisc | 5622 | 370.765s |
| shell_batch | 4078 | 286.024s |
| veth | 1935 | 136.123s |
| ip | 1922 | 98.842s |
| link | 387 | 21.564s |
| addr | 387 | 20.785s |

Host apply operation counts:

| Operation | Count |
| --- | ---: |
| `update_slot_parameters` | 1664 |
| `rebind_slot_attachment` | 573 |
| `activate_slot_attachment` | 1 |

Affected host apply scope:

- Apply results: 2238 total; avg 14.53 per RuntimeEpoch, p50 15, p95 18, max 19.
- Affected nodes from bindings/endpoints: 188.
- Slot types: 1264 `ut_access`, 638 `gw_access`, 336 `cross_plane_isl`.
- qdisc per `update_slot_parameters`: avg 0.132s, p50 0.128s, p95 0.167s,
  max 0.286s; sum 220.031s.

Conclusion label: evidence-supported bottleneck.

Host/direct apply is the largest single steady RuntimeEpoch component. Within it,
qdisc wall dominates, followed by shell-batch lifecycle/address/link work and
veth lifecycle.

## Node-Local Linux/MPLS Attribution

Steady RuntimeEpoch node-local apply, A run:

- Apply wall: avg 1.257s, p50 1.181s, p95 2.490s, max 3.912s; sum 193.584s.
- Apply commands: 4580 total; avg 29.74 per RuntimeEpoch, p50 26, p95 63, max
  105.
- Affected node count: 114.
- Per-node batch wall: avg 0.422s, p50 0.397s, p95 0.670s, max 0.995s.

Command families:

| Family | Count |
| --- | ---: |
| route | 1470 |
| iptables | 1260 |
| sysctl | 942 |
| mpls_route | 488 |
| ip | 420 |

Semantic scope split:

| Scope | Count |
| --- | ---: |
| satellite-core G/Z MPLS-like forwarding | 1118 |
| edge NAT / iptables | 1260 |
| MPLS readiness sysctl | 942 |
| edge source ip rule | 420 |
| edge UT default route | 420 |
| edge UT delivery route | 420 |

Conclusion label: not currently dominant.

Satellite-core Linux MPLS/LFIB forwarding updates are still material, but they
are not the dominant current bottleneck. The entire node-local Linux/MPLS apply
is 17.1% of steady RuntimeEpoch wall sum. Satellite-core forwarding is a
sub-scope of that 17.1%, and by command count it is comparable to, not larger
than, edge NAT/iptables plus readiness work.

Important evidence gap: node-local timing is batch-level, not per-command. We
can count MPLS route, route, iptables, and sysctl commands, and we can time the
whole apply batch, but we cannot assign wall seconds to individual node-local
command families without a targeted instrumentation change.

Conclusion label for per-family node-local wall: likely bottleneck but
insufficient evidence.

## qdisc And ParameterEpoch

ParameterEpoch host apply is qdisc-only in current typed metrics:

- qdisc commands: 3122.
- qdisc wall: 204.964s.
- operations: 1561 `update_slot_parameters`.
- affected nodes from bindings/endpoints: 178.

This confirms qdisc delay/rate/bandwidth remains a major direct backend cost.
BPF's current parameter map owns only active/loss/drop/counter fields, not
delay/rate/bandwidth.

Conclusion label: evidence-supported bottleneck for qdisc/direct backend work;
not a BPF MPLS replacement target unless BPF also grows parameter timing/rate
semantics.

## Verify, Ping, Audit, Rollback

Steady RuntimeEpoch validation/audit:

- post-apply validation probe: effectively zero in this run due sampled/skipped
  validation policy.
- artifact/audit blocking write: effectively zero.
- post-run preflight: 8.138s once.

Rollback/cleanup:

- steady node-local incremental cleanup: avg 2.363s, p50 2.137s, p95 5.094s,
  max 8.145s; sum 363.856s.
- final node-local rollback: 1260.401s, with 20456 requested and 16357 grouped
  cleanup commands.
- final host rollback: 90.789s.
- final host artifact cleanup: 113.330s, dominated by veth delete/down
  109.237s.

Conclusion label: evidence-supported bottleneck for cleanup/rollback.

Cleanup is a major steady RuntimeEpoch cost and a very large final teardown cost.
BPF replacement of forward MPLS alone will not remove direct veth cleanup or
node-local cleanup unless ownership and rollback semantics change too.

## Instrumentation Sufficiency

Sufficient today:

- RuntimeEpoch phase-level timing: host apply, node-local apply, cleanup,
  validation, artifact write.
- Host/direct typed operation metrics: command count and wall by family,
  operation counts, slot types, qdisc per-result latency.
- ParameterEpoch qdisc command count and wall.
- Node-local command count by family and by semantic scope.

Insufficient today:

- Node-local per-command wall by family. `apply_typed_operation_metrics` uses
  `timing_granularity=batch_wall_seconds_command_counts`, so route vs iptables
  vs MPLS route wall cannot be separated.
- Per RuntimeEpoch planner/FEC diff subphase. It is folded into
  `runtime_unclassified_wall_seconds`.
- Host `shell_batch` internal split. It likely contains link/address/lifecycle
  work, but the shell payload is not timed by subcommand.

Minimal future instrumentation, if needed:

- Add node-local per-command timing inside the batch executor, preserving current
  batch summaries.
- Add a planner/FEC-diff timer around RuntimeEpoch plan/diff construction.
- Split host shell-batch subcommands into typed subcommand metrics or avoid
  batching when running diagnostic mode.

No runtime instrumentation was changed in this pass.

## Conclusion Labels

Evidence-supported bottleneck:

- Host/direct NetworkMutationPlan apply, especially qdisc and shell-batch
  lifecycle/address/link work.
- Node-local incremental cleanup and final cleanup/rollback.
- qdisc ParameterEpoch and RuntimeEpoch parameter work.

Likely bottleneck but insufficient evidence:

- Some subset of node-local route/MPLS/iptables/sysctl commands, but per-family
  wall is unavailable.

Not currently dominant:

- Satellite-core Linux MPLS/LFIB forwarding updates as a standalone target. They
  are material, but current evidence says qdisc/direct lifecycle/cleanup dominate
  steady RuntimeEpoch wall.
- Audit/artifact writes and sampled validation probes.

Unknown / needs targeted run:

- Exact planner vs FEC diff vs artifact prep split inside the 0.597s avg
  RuntimeEpoch unclassified residual.
- Exact node-local per-command p50/p95/max wall by command family.

## BPF Replacement Implication

Do not enter BPF-owned replacement expecting large gains from eliding only
satellite-core MPLS/LFIB commands.

The best-supported first replacement target is still useful but narrower:

- BPF-owned forward G/Z forwarding can reduce node-local Linux/MPLS apply only
  inside its owned scope.
- It will not address the largest current host/direct costs: qdisc, veth/link
  lifecycle, shell-batch address/link work, and cleanup.
- To make replacement measurable, the implementation needs explicit elision
  counters and command-count deltas, plus better node-local per-command timing.
