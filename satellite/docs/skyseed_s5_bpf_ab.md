# Skyseed S5 BPF Full-Scope A/B Evidence

Goal 20 ran the S5 172-satellite canonical workload at current HEAD with the
same 3600s horizon/profile for both groups:

- A: BPF off, canonical Linux/direct backend.
- B: `--bpf-backend experimental --bpf-scope full_eligible_scope`
  with `--bpf-max-scope-entries 4096`.

Machine-readable compact summary:
`satellite/baselines/skyseed_s5_bpf_ab_summary.json`

## Duration Decision

The existing 2h baseline
`satellite/output/starlink_shell_experiments/gz-z6metadata-2h-gated-20260611-0824`
has 324 steady RuntimeEpoch samples and 275 ParameterEpoch samples. Its first
3600s simulated window has enough samples for p50/p95 evidence: 155 steady
RuntimeEpoch samples and 145 ParameterEpoch samples.

Decision: use 3600s for both A and B. Both runs used the same S5 planned-shell
profile, 172 satellites, 12 UTs, 6 gateways, same catalogs, same thresholds, and
same ping watch.

## Run Dirs

| Group | Run dir |
| --- | --- |
| A, BPF off | `satellite/output/starlink_shell_experiments/goal20-s5-bpf-ab-a-off-3600-20260621-r2` |
| B, BPF full eligible scope | `satellite/output/starlink_shell_experiments/goal20-s5-bpf-ab-b-fullscope-3600-20260621` |

The earlier A attempt
`goal20-s5-bpf-ab-a-off-3600-20260621` was interrupted by the VM reboot and is
not used as evidence.

## Correctness

Both evidence runs passed the required checks:

| Check | A | B |
| --- | --- | --- |
| `real_apply_success` | true | true |
| `ping_success` | true | true |
| `rollback_completed` | true | true |
| post-run preflight clean | true | true |
| temporal audit pass | true | true |
| regression checker hard failures | 0 | 0 |

Regression checker passed for both runs. Both also carried warning-level p95
runtime/host-apply regressions against the older 2h baseline; these warnings
apply to both A and B and do not distinguish the BPF backend.

Post-run external preflight after B was clean: no compose containers, no named
Skyseed containers/networks, no direct backend host interfaces, and no runtime
lock files.

## Epoch Counts

| Metric | A | B |
| --- | ---: | ---: |
| RuntimeEpoch including initial | 155 | 155 |
| RuntimeEpoch steady-state | 154 | 154 |
| ParameterEpoch | 145 | 145 |
| Replenishments | 11 | 11 |

## Timing

Steady RuntimeEpoch timing:

| Metric | A p50 / p95 / max | B p50 / p95 / max | Delta |
| --- | ---: | ---: | ---: |
| Runtime wall | 6.612210 / 14.188878 / 26.198900s | 6.356001 / 13.969142 / 25.354385s | p50 -3.875%, p95 -1.549%, max -3.223% |
| Runtime host apply | 2.694735 / 5.923886 / 13.544707s | 2.630142 / 5.853614 / 12.874790s | p50 -2.397%, p95 -1.186%, max -4.946% |
| Runtime forwarding apply | 4.026027 / 8.123764 / 17.456581s | 3.958742 / 8.059609 / 17.160989s | p50 -1.671%, p95 -0.790%, max -1.693% |
| Runtime node-local apply | 1.181226 / 2.489691 / 3.911874s | 1.192452 / 2.472835 / 4.286199s | p50 +0.950%, p95 -0.677%, max +9.569% |

Mean Runtime wall did not improve: A 7.356255s, B 7.404383s (+0.654%).
Mean host apply did improve slightly: A 3.139250s, B 3.096244s (-1.370%).

ParameterEpoch timing:

| Metric | A p50 / p95 / max | B p50 / p95 / max | Delta |
| --- | ---: | ---: | ---: |
| Parameter wall | 1.269957 / 1.808806 / 4.710750s | 1.282958 / 1.727904 / 4.404379s | p50 +1.024%, p95 -4.473%, max -6.504% |
| Parameter host apply | 1.325367 / 2.203557 / 2.355980s | 1.305274 / 2.150991 / 2.453683s | p50 -1.516%, p95 -2.386%, max +4.147% |
| Parameter qdisc apply | 1.314676 / 2.182181 / 2.344267s | 1.293469 / 2.139137 / 2.442002s | p50 -1.613%, p95 -1.973%, max +4.169% |

This is consistent with the scope boundary: delay/rate/bandwidth remain in
qdisc/netem, so ParameterEpoch is not expected to show a large BPF benefit.

## Typed Operations

The A and B operation surfaces were intentionally identical.

This is the key replacement boundary for Goal 20: the BPF experimental backend
was an overlay/canary path, not a host-apply replacement. It loaded maps,
attached tc-BPF, exercised a bounded dataplane probe, and rolled back after the
canonical epoch loop had already run. It did not elide NetworkMutationPlan,
Linux/MPLS, qdisc, veth, route, rule, or iptables operations.

Runtime steady-state:

- Host apply results: 2238 in A and 2238 in B.
- Operations: 1 `activate_slot_attachment`, 573 `rebind_slot_attachment`,
  1664 `update_slot_parameters` in both runs.
- Slot types: 336 `cross_plane_isl`, 638 `gw_access`, 1264 `ut_access` in both.
- Host typed command count: 14331 in both.
- Host typed wall total: A 934.102685s, B 921.676236s (-12.426449s).
- Major wall families:
  - A: qdisc 370.764605s, shell_batch 286.024299s, veth 136.122603s.
  - B: qdisc 369.803272s, shell_batch 279.412544s, veth 131.620163s.

ParameterEpoch:

- Host apply results: 1561 in A and 1561 in B.
- Operations: 1561 `update_slot_parameters` in both.
- Slot types: 199 `cross_plane_isl`, 459 `gw_access`, 903 `ut_access` in both.
- Host typed command count: 3122 qdisc commands in both.
- Host typed wall total: A 204.963577s, B 202.943356s (-2.020221s).
- Trigger reasons: 73 `bandwidth_threshold_exceeded` and 145
  `routing_cost_changed` in both.

## BPF Coverage And Counters

B full-scope backend completed successfully:

- `ok=true`
- `bpf_scope=full_eligible_scope`
- `canonical_runtime_unchanged=true`
- `linux_direct_backend_replaced=false`
- `qdisc_timeline_backend_status=blocked_or_trace_only`
- `bpf_rollback_completed=true`
- rollback residue clean=true
- attached interfaces before rollback: 10
- attached interfaces after rollback: 0

Loaded full eligible scope:

| Counter | Value |
| --- | ---: |
| `loaded_runtime_generation_count` | 25 |
| `loaded_gz_fec_count` | 268 |
| `loaded_parameter_generation_count` | 145 |
| `loaded_parameter_link_count` | 19 |
| `loaded_parameter_entry_count` | 1561 |
| `active_generation_switch_count` | 2 |

Packet counters:

| Counter | Value |
| --- | ---: |
| redirect | 3 |
| pass | 650 |
| drop | 1 |
| canonical fallback | 650 |
| fail-closed | 1 |
| runtime generation hit | 4 |
| FEC hit | 4 |
| parameter generation hit | 4 |
| parameter entry hit | 3 |

Parameter fields in BPF were `active`, `loss_ppm`, `drop`, and counters.
`delay_ms`, `rate_bps`, and `bandwidth_mbps` remained excluded and stayed on
canonical qdisc/netem.

## Fallback Classification

Evidence-backed:

- BPF full-scope maps were loaded, attached, exercised, and rolled back cleanly.
- There is positive BPF signal: redirects, runtime/FEC/parameter hits, and active
  generation switches.
- Non-eligible/control traffic fallback is acceptable.

Caveat:

- Observed packet counters are dominated by canonical fallback: 650 fallback/pass
  packets vs 10 eligible BPF signal events (`redirect + fec_hit +
  parameter_entry_hit`).
- The report does not classify each fallback packet by eligibility, so the run
  does not prove that eligible traffic is mostly handled by BPF.
- Treat the fallback distribution as a coverage caveat, not a correctness
  failure.

## Cleanup

| Metric | A | B |
| --- | ---: | ---: |
| cleanup success | true | true |
| node-local rollback | 1260.401274s | 1267.384420s |
| host rollback | 45.550085s | 45.861988s |
| host artifact cleanup | 113.330443s | 113.334249s |
| post-run preflight | 8.137705s | 8.666189s |
| remaining host interfaces | 0 | 0 |

BPF-specific rollback also completed and left no clsact interface hits or pinned
map residue.

## G/Z Audit

Both runs preserved the shared G/Z backbone model:

- current realization: `shared_gz_backbone`
- current return state does not scale with flow count
- runtime zone-return boundary records: 310
- max all/reapplied intents: 5
- backbone ratio: 1.0

No G/Z semantic change was made for this A/B.

## Conclusion

Evidence-backed:

- BPF full eligible scope is stable for this S5 172-sat 3600s run.
- Correctness did not regress: real apply, ping, rollback, temporal audit, and
  post-run preflight were clean for both A and B.
- Runtime host apply has a small improvement in B: p50 -2.397%, p95 -1.186%,
  max -4.946%, mean -1.370%.
- Runtime wall p50/p95/max also improve modestly, but mean wall is essentially
  flat/slightly worse (+0.654%).

Caveats:

- The performance gain is limited, not broad. Runtime forwarding apply is nearly
  unchanged, node-local apply is not improved on average, and ParameterEpoch
  remains qdisc/netem dominated.
- BPF counters show canonical fallback dominates observed packet counters, so
  eligible-traffic BPF coverage is not proven by this run.
- The most defensible conclusion is: BPF full-scope backend is stable and
  correctness-clean, with limited evidence of RuntimeEpoch host-apply benefit;
  it is not yet evidence-backed as a large or end-to-end performance win.
