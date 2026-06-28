# Skyseed Runtime Bottleneck Instrumentation Sanity

Date: 2026-06-21

Scope: short bounded measurement validation only. This run did not implement
BPF-owned replacement, qdisc/TheaterQ optimization, or G/Z semantic changes. It
did not run S5 A/B or staged acceptance.

Machine-readable summary:
`satellite/baselines/skyseed_runtime_bottleneck_instrumentation_sanity_summary.json`

Evidence run:
`satellite/output/starlink_shell_experiments/goal-runtime-bottleneck-hardening-sanity-20260621`

Bounded workload:

- `24` satellites, `2` UTs, `3` gateways.
- BPF backend off.
- Selected execution: `1` RuntimeEpoch and `1` ParameterEpoch.
- Outcome: `real_apply_success=true`, `ping_success=true`,
  `rollback_completed=true`, `post_run_clean=true`.

## Evidence-Supported Observations

The new node-local timing instrumentation worked on this run:

- RuntimeEpoch node-local apply: `40/40` commands had per-command timings.
- Final node-local cleanup: `39/39` commands had per-command timings.
- Timing marker lines remaining in stored stdout: `0` for apply and cleanup.
- Allowed cleanup failures did not fail the run: `9` gateway iptables cleanup
  commands returned `127`, but rollback still succeeded. These were
  failed-but-allowed cleanup commands, not runtime failures.

RuntimeEpoch phase wall, direct critical-path evidence:

| Phase | Wall |
| --- | ---: |
| RuntimeEpoch total transition | 7.691751s |
| NetworkMutationPlan / host direct apply | 4.075139s |
| Node-local apply | 1.594342s |
| Node-local incremental cleanup | 0.000000s |
| Post-transition connectivity probe | 1.286420s |
| Required safety gate | 1.757232s |

Node-local apply per-scope timing, direct per-command evidence:

| Scope | Count | Per-command wall |
| --- | ---: | ---: |
| satellite-core MPLS/LFIB | 12 | 0.086073s |
| ip route / ip rule | 7 | 0.123655s |
| edge NAT / iptables | 9 | 0.201645s |
| MPLS readiness sysctl | 12 | 0.182557s |

Final node-local cleanup per-scope timing, direct marker timing plus
description-based cleanup classification:

| Cleanup scope | Count | Per-command wall |
| --- | ---: | ---: |
| satellite-core MPLS/LFIB residue | 12 | 0.104777s |
| route/rule residue | 7 | 0.058943s |
| edge iptables/SNAT residue | 9 | 0.194115s |
| readiness/sysctl cleanup or checks | 11 | 0.178271s |

The final cleanup report did not retain raw rollback commands, so final cleanup
scope classification uses descriptions plus command timing markers. Apply
classification uses the full batch plan and commands.

## Cumulative, Not Critical Path

Do not add cumulative worker command wall and present it as RuntimeEpoch wall.

Runtime host/direct typed command wall:

| Family | Count | Cumulative wall |
| --- | ---: | ---: |
| shell_batch | 182 | 6.027986s |
| qdisc | 64 | 1.116801s |
| ip | 64 | 0.889086s |

This sums to `8.033873s`, while the host/direct apply phase wall was
`4.075139s`; the difference is expected because host workers run in parallel.

Runtime node-local apply had `3.121925s` cumulative batch wall, while the
node-local apply phase wall was `1.594342s`. Again, this is cumulative worker
evidence, not an additive critical path total.

ParameterEpoch qdisc was small but cleanly measured:

- phase wall: `0.097217s`
- qdisc command count: `6`
- qdisc cumulative wall: `0.096949s`

Final cleanup lifecycle costs were visible but are teardown evidence, not live
RuntimeEpoch wall:

- host rollback: `364` commands / `11.352783s` cumulative command wall,
  including `128` qdisc commands and `236` shell_batch commands.
- host artifact cleanup: `188` commands / `7.705595s` cumulative command wall,
  dominated by `158` veth commands / `7.264985s`.

## Unknowns Remaining

This sanity run did not exercise live RuntimeEpoch incremental cleanup. The
temporal planner selected only one RuntimeEpoch, so
`node_local_incremental_cleanup_wall_seconds=0.0`. Final cleanup attribution is
valid teardown evidence, but it must not be treated as live RuntimeEpoch cleanup
wall.

Still unknown:

- live RuntimeEpoch incremental cleanup per-scope wall;
- host `shell_batch` subcommand breakdown;
- planner / FEC diff subphase wall.

## Optimization Read

BPF-owned satellite-core G/Z forwarding:

- evidence-supported for reducing measured apply scope count: `12`
  satellite-core MPLS/LFIB apply commands in this sanity;
- evidence-supported for reducing measured final cleanup scope count: `12`
  satellite-core MPLS/LFIB residue cleanup commands;
- live incremental cleanup benefit still needs a targeted two-RuntimeEpoch run.

qdisc elision:

- evidence-supported candidate: runtime qdisc was `64` commands /
  `1.116801s` cumulative wall, and ParameterEpoch qdisc was measured directly;
- qdisc appears on the RuntimeEpoch critical path only through the broader
  host/direct apply phase, not as a standalone additive wall bucket.

lifecycle / shell_batch optimization:

- strong immediate candidate in this small run by cumulative wall:
  runtime `shell_batch=6.027986s`, final host artifact cleanup
  `veth=7.264985s`;
- exact target selection still needs shell_batch subcommand timing.

cleanup scope narrowing:

- evidence-supported for final cleanup;
- live RuntimeEpoch cleanup scope narrowing remains unknown until a bounded run
  actually produces nonzero incremental cleanup.
