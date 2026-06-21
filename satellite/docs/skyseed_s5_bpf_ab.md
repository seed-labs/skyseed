# Skyseed S5 BPF A/B Readiness

Goal 19 asked for an S5 172-satellite canonical workload A/B:

- A: current HEAD, BPF off, canonical Linux/direct backend.
- B: current HEAD, BPF experimental backend on, full eligible scope.

The full A/B was not executed. Current HEAD cannot run the B group at the required
full eligible BPF scope. Per the goal constraint, this stops before A/B execution
instead of downgrading to a single UT/FEC/path canary.

Machine-readable compact summary:
`satellite/baselines/skyseed_s5_bpf_ab_summary.json`

## Duration Decision

Source baseline:
`satellite/output/starlink_shell_experiments/gz-z6metadata-2h-gated-20260611-0824`

The 2h baseline completed cleanly:

- `real_apply_success=true`
- `ping_success=true`
- `rollback_completed=true`
- `post_run_preflight clean_after_run=true`
- temporal audit clean for future active-link state, future forwarding state,
  ParameterEpoch topology-mutation separation, and RuntimeEpoch mutation scope
- regression checker passed against
  `satellite/baselines/skyseed_1shell_6z_2h_regression_baseline.json`

Cold start and initial materialization are not steady-state:

- cold start before first epoch execution: 579.663337s
- first RuntimeEpoch transition: 34.173132s
- steady-state RuntimeEpoch explicitly excludes the first RuntimeEpoch

Full 2h steady-state baseline:

| Metric | Count | p50 | p95 | max |
| --- | ---: | ---: | ---: | ---: |
| RuntimeEpoch wall | 324 | 3.861377s | 7.965185s | 15.870726s |
| RuntimeEpoch host apply | 324 | 1.925403s | 4.392757s | 8.564896s |
| RuntimeEpoch node-local apply | 324 | 0.542561s | 1.432611s | 2.751492s |
| ParameterEpoch wall | 275 | 0.820086s | 1.278064s | 2.234283s |
| ParameterEpoch host apply | 275 | 0.556252s | 0.973226s | 1.388010s |
| ParameterEpoch qdisc apply | 275 | 1.050617s | 1.878107s | 2.581523s |

Shorter-window evidence:

| Simulated window | RuntimeEpoch count | ParameterEpoch count | Runtime wall p50/p95 | Parameter wall p50/p95 |
| ---: | ---: | ---: | ---: | ---: |
| 1800s | 80 | 70 | 3.374019s / 7.324847s | 0.723688s / 1.055478s |
| 2700s | 116 | 109 | 3.508571s / 7.331265s | 0.742865s / 1.020608s |
| 3600s | 155 | 145 | 3.633134s / 7.378600s | 0.740864s / 1.075073s |
| 5400s | 248 | 202 | 3.794137s / 8.071460s | 0.775333s / 1.164706s |
| 7140s | 324 | 275 | 3.861377s / 7.965185s | 0.820086s / 1.278064s |

Decision if the BPF full-scope backend becomes available:

- Recommended A/B duration: same 3600s simulated horizon for A and B.
- Acceptable shorter floor for smoke-level A/B: 2700s simulated horizon.
- Basis: after excluding cold start and initial materialization, 2700s already has
  116 steady RuntimeEpoch and 109 ParameterEpoch samples; 3600s has 155 and 145.
- 2h is not required for initial p50/p95 A/B. Max-tail claims remain caveated in
  shorter windows and should not be overclaimed.

## Full Eligible Scope Blocker

Current HEAD's live BPF path is still canary/subset scoped:

- `satellite/bpf_dataplane_canary.py` normalizes `max_scope_entries` to at most 16.
- `select_experimental_scope()` returns `scope=semantic_intent_canary_subset`.
- `load_canary_maps()` loads route and parameter entries only for selected target
  IPs from that scope.
- `satellite/experimental/bpf/skyseed_tc_dataplane_canary.c` has live map
  capacities:
  - `generation_status.max_elem=64`
  - `fec_forwarding.max_elem=16`
  - `param_status.max_elem=64`
  - `parameter_values.max_elem=16`

The S5 baseline compiles the following eligible BPF scope:

| Scope item | Required | Current live limit | Status |
| --- | ---: | ---: | --- |
| Runtime generations | 25 | 64 | fits |
| G/Z FEC entries | 268 | 16 | blocked |
| Parameter generations | 275 | 64 | blocked |
| Parameter links | 21 | selector max 16 | blocked |
| Parameter entries | 3243 | 16 | blocked |

Delay/rate/bandwidth are intentionally left to qdisc/netem and are not counted as
a blocker here.

## Fallback Classification

No B run was executed, so there are no BPF packet counters from a full-scope run.
The fallback classification is source-level only:

- Eligible traffic beyond the selected canary target IP entries would not be BPF
  controlled because `fec_forwarding` is populated only for selected target IPs.
- Missing `fec_forwarding` entries return `TC_ACT_OK`, counted as canonical
  fallback in the BPF program.
- Non-IP traffic also falls through to canonical fallback.
- Missing active generation, missing parameter generation, missing parameter
  entry, inactive FEC, inactive parameter, loss-drop, and bad packet cases are
  fail-closed drop paths.

This fails the Goal 19 requirement that eligible BPF-controlled traffic should
not mainly fallback. Running it would only produce canary evidence, not a valid
full-scope BPF A/B.

## A/B Status

| Group | Run dir | Status |
| --- | --- | --- |
| A, BPF off | none | not run because B group is blocked |
| B, BPF on full eligible scope | none | blocked before run |

Evidence-backed conclusion:

- BPF backend benefit is not proven.
- The blocker is evidence-backed from current HEAD source and compiled S5 scope.
- The duration decision is evidence-backed from the existing 2h baseline.

Caveats:

- No BPF full-scope counters exist because the B group was not run.
- No A/B timing delta exists because executing A alone would not answer the A/B
  question under the goal constraints.
- A future full-scope implementation should rerun A and B with the same
  3600s horizon, same S5 canonical workload, rollback checks, post-run preflight,
  G/Z audit, and regression checker.
