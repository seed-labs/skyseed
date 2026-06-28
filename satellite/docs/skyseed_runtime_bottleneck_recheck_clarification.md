# Skyseed Runtime Bottleneck Recheck Clarification

Date: 2026-06-21

Scope: clarification only. No runtime optimization, no long runtime run, no G/Z
semantic change, no BPF-owned replacement.

## Evidence Sources

Primary evidence is the Goal 20 S5 3600s BPF-off run:

`satellite/output/starlink_shell_experiments/goal20-s5-bpf-ab-a-off-3600-20260621-r2`

Cross-check evidence is the Goal 20 S5 3600s BPF full-scope overlay run:

`satellite/output/starlink_shell_experiments/goal20-s5-bpf-ab-b-fullscope-3600-20260621`

Files used:

- `timing.json`: steady RuntimeEpoch phase wall stats and phase-share
  denominator.
- `epoch_execution_details.jsonl`: host typed metrics, node-local batch plans,
  node-local apply/cleanup timings, command descriptions.
- `real_runtime_report.json`: final cleanup / rollback timings.
- `runtime_operation_metrics.py`: command-family classifier semantics.

No 2h baseline, post-cleanup optimization run, new sanity run, Tier 1, or Tier 2
acceptance was used for this clarification.

## Timing Categories

The RuntimeEpoch phase timings are the wall-clock attribution fields:

- host/direct `NetworkMutationPlan` apply
- node-local Linux/MPLS apply
- node-local incremental cleanup
- validation/audit/artifact write
- residual unclassified runtime wall

These are intended to describe sequential RuntimeEpoch wall phases. They are the
right source for percentages such as host apply 42.7%, node-local apply 17.1%,
and cleanup 32.1%.

The host typed families are different: `qdisc`, `shell_batch`, `veth`, `ip`,
`link`, and `addr` are cumulative command-level wall sums inside host apply.
They are mutually exclusive at the top-level command classifier, but they are
not a wall-clock decomposition of RuntimeEpoch time. Because host apply uses
bounded parallelism, cumulative command wall can exceed phase wall. In the A run:

- host RuntimeEpoch phase wall sum: 483.444s
- host typed cumulative command wall sum: 934.103s

So `qdisc 370.765s` is evidence that qdisc commands dominate host command wall,
not that qdisc alone consumed 370.765s of serialized RuntimeEpoch wall.

## shell_batch Meaning

`shell_batch` means the command classifier saw a shell payload with multiple
subcommands and did not split it into a more specific top-level family.

In host/direct apply, `shell_batch` mostly represents direct lifecycle/address
work emitted by `network_runtime.py`, for example:

- host-side `ip link set ... master ... && ip link set ... up`
- namespace `ip addr flush ... || true; ip addr add ... && ip link set ... up`
- deactivate/down/flush/nomaster shell payloads

It is not evidence of node-local MPLS, route, iptables, or sysctl work; those are
recorded in node-local batch metrics, not host/direct typed metrics. It also does
not include qdisc in the observed host apply path, because qdisc is emitted as
direct `tc qdisc ...` commands and classified as `qdisc`.

Potential caveat: `shell_batch` hides subcommand-level timing, so lifecycle vs
address vs link cost inside that family remains a blind spot.

## Percent Calculation

The statement:

`steady RuntimeEpoch host/direct NetworkMutationPlan apply avg 3.139s, 42.7%`

uses only steady RuntimeEpochs from the A run, excluding the first
materialization RuntimeEpoch:

- numerator: sum of `semantic_runtime_host_apply_seconds` from `timing.json`
  steady RuntimeEpoch rows = 483.444435s
- denominator: sum of `semantic_total_epoch_wall_seconds` from the same rows =
  1132.863334s
- percentage: 483.444435 / 1132.863334 = 42.675%

The denominator is total steady RuntimeEpoch wall and therefore includes cleanup,
validation/probe time, scheduler blocking after dataplane ready, artifact/audit
write, and unclassified residual wall as recorded in `timing.json`.

## qdisc 370.765s

`qdisc 370.765s` comes from steady RuntimeEpoch host typed metrics in
`epoch_execution_details.jsonl` for the A run:

- field path:
  `host_mutation_execution.outcome.typed_operation_metrics.wall_seconds_by_family.qdisc`
- aggregation: sum across the 154 steady RuntimeEpoch records
- scope: RuntimeEpoch host/direct apply only
- commands: top-level `tc qdisc ...` commands classified by
  `runtime_operation_metrics.py`
- not included: ParameterEpoch qdisc commands

ParameterEpoch qdisc is separate:

- 3122 qdisc commands
- 204.964s cumulative command wall

The qdisc timing is per-command wall captured by host direct runtime
instrumentation, not batch-level inference.

## Node-Local Incremental Cleanup

`node-local incremental cleanup avg 2.363s, 32.1%` comes from steady
RuntimeEpoch `node_local_incremental_cleanup_wall_seconds`.

It runs only when flows from the previous active session are no longer active or
must be cleaned before applying changed flows. In the A run:

- nonzero cleanup epochs: 129
- zero cleanup epochs: 25
- steady RuntimeEpoch count: 154

It cleans node-local Linux/MPLS runtime state for removed/replaced flow contexts,
while preserving shared G/Z zone-return target keys that are still active. A
representative cleanup plan contains:

- UT default route delete
- serving-satellite policy-table route delete
- serving-satellite UT delivery route delete
- serving-satellite source `ip rule` delete
- MPLS input/platform-label restoration
- gateway NAT/iptables deletes when not preserved
- forward/reverse LFIB cleanup when not preserved

It is not qdisc or veth cleanup; those belong to host/direct runtime cleanup.
Final host artifact cleanup is separate and is dominated by veth deletion.

## Node-Local Apply And Core Forwarding

`node-local Linux/MPLS apply avg 1.257s, 17.1%` is the whole node-local apply
phase: route, MPLS route, ip rule, iptables, sysctl, and related edge commands.

The `satellite_core_gz_mpls_forwarding=1118` count is a semantic command-count
classification from node-local apply batch plans. It includes commands whose
descriptions/shape correspond to G/Z/MPLS-like forwarding:

- serving-satellite policy-table default route with `encap mpls`
- gateway forward pop MPLS route
- shared gateway zone-return ingress route with `encap mpls`
- shared serving-satellite reverse pop
- transit MPLS LFIB route where present

It does not include:

- `edge_nat_iptables=1260`
- `edge_ip_rule=420`
- `edge_ut_default_route=420`
- `edge_ut_delivery_route=420`
- `mpls_readiness_sysctl=942`

The 1118 count includes forward and reverse/shared compatibility forwarding
commands where those are expressed as MPLS-like route installs. It is not
"satellite core only, forward only." It is better read as
`G/Z MPLS-like forwarding command count`.

Because node-local timing is batch-level, the 1118 commands do not have their own
wall-time total. The evidence-supported wall statement is only for the whole
node-local apply phase.

## MPLS Readiness sysctl

`MPLS readiness sysctl=942` is node-local apply command count, not host
`shell_batch`.

It is high because `_apply_specs()` emits readiness restoration/setup commands
based on per-context preflight state:

- `net.mpls.platform_labels=...`
- `net.mpls.conf.<if>.input=1`

These commands can repeat across RuntimeEpochs when changed/reapplied contexts
touch the same nodes/interfaces and the captured preflight does not already show
the desired state. The count is therefore related to changed flow contexts, not a
single one-time setup.

It may be a candidate for cached readiness or one-time setup, but that is an
optimization question outside this clarification. It would require preserving
correct rollback semantics and proving that readiness state is safe to hold
across epochs.

## Edge NAT / iptables

`edge_nat_iptables=1260` is node-local apply command count from gateway FORWARD
and SNAT rule specs.

It is related to UT/GW scale and changed/reapplied flow count. In the A run,
node-local apply sees:

- 154 steady RuntimeEpochs
- avg 2.727 reapplied flows per RuntimeEpoch
- 420 edge source-rule / UT-route-scale events
- 1260 iptables specs, which is three gateway edge rules per applied UT flow
  context: forward filter, reverse filter, SNAT

These rules are emitted with check-then-add shell payloads, so the top-level
metric counts the shell spec once, not each possible `iptables -C` and
`iptables -A` subcommand separately.

There may be no-op elision potential, but the current code still emits these
specs for reapplied contexts. Proving safe elision would need an ownership/cache
contract for gateway edge rules and rollback.

## Current Blind Spots

Evidence-supported:

- host/direct apply is a major RuntimeEpoch bottleneck.
- host qdisc is the largest host command-wall family.
- node-local incremental cleanup is a major RuntimeEpoch bottleneck.
- node-local Linux/MPLS apply is material but smaller than host/direct apply and
  cleanup.
- satellite-core/G/Z MPLS-like forwarding commands are not dominant by command
  count over all node-local edge/readiness commands.

Batch-level inference:

- node-local command scope counts distinguish core forwarding, edge route/rule,
  iptables/NAT, and readiness sysctl, but wall time is available only for the
  whole node-local batch/apply phase.
- per-node batch wall provides a rough node-level signal, not command-family
  latency.

Unknown:

- per-command node-local wall time for MPLS route vs route vs iptables vs sysctl.
- `shell_batch` subcommand wall split for host lifecycle/address/link operations.
- exact per-epoch planner/FEC diff time separate from
  `runtime_unclassified_wall_seconds`.

## Minimal Instrumentation Proposal

Do not implement yet. If needed later, add only diagnostic instrumentation:

1. Node-local per-command timing:
   - time each `RuntimeCommandSpec` inside `_run_node_local_batch`
   - preserve existing batch execution and summaries
   - emit `wall_seconds_by_family` and p50/p95/max by command family

2. Host shell-batch subcommand breakdown:
   - in diagnostic mode, split shell payload classification into subcommand
     counters, or record parsed subcommands under the `shell_batch` entry
   - keep existing command execution unchanged

3. Planner/FEC diff timer:
   - add timers around RuntimeEpoch plan/diff construction before host apply
   - report `planner_wall_seconds`, `fec_diff_wall_seconds`,
     `artifact_prepare_wall_seconds`

Until those exist, BPF replacement claims should use phase-level wall and
command-count deltas, not per-command node-local wall deltas.
