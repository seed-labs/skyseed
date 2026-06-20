# Skyseed ParameterEpoch Qdisc Timeline

Goal 12 compiles Skyseed ParameterEpoch link-parameter updates into a
backend-neutral qdisc timeline representation and evaluates whether a
TheaterQ-style isolated demo is a good next step. It does not change the
canonical runtime, does not change RuntimeEpoch or ParameterEpoch scheduling,
does not attach to the real dataplane, does not run a long real-runtime
experiment, and does not load any external kernel module.

Artifacts:

- `satellite/apps/compile_parameter_timeline_to_qdisc.py`
- `satellite/baselines/skyseed_parameter_qdisc_timeline_summary.json`
- `satellite/test_parameter_qdisc_timeline_compiler.py`

## Input

The baseline was compiled from the existing 2h Skyseed G/Z artifact:

```text
satellite/output/starlink_shell_experiments/gz-z6metadata-2h-gated-20260611-0824
```

The compiler reads:

- `temporal_plan.json`, for ParameterEpoch metadata when available;
- `parameter_epoch_summary.json`, for compact artifact summary metadata;
- `epoch_execution_details.jsonl`, for the real per-slot
  `update_slot_parameters` records.

The actual link parameters come from
`host_mutation_execution.apply_results[].resulting_state.runtime_parameters`.
Those records include the slot/link id, activation time, `delay_ms`,
`bandwidth_mbps`, `loss_percent`, and `routing_cost`. No file under
`satellite/output` is copied into the repository baseline.

## BPF Versus Qdisc

Goal 11 intentionally kept BPF focused on packet-decision state:

- active gate;
- loss/drop decision;
- counters;
- packet marks or audit metadata;
- fail-closed lookup behavior.

The qdisc side owns time-dependent queue behavior:

- delay;
- bandwidth/rate limiting;
- queue size and queue occupancy policy;
- reordering policy;
- future dynamic qdisc replay.

Goal 12 only compiles and audits the qdisc-side timeline shape. It is not an
optimized ParameterEpoch backend landing.

## Timeline Shape

The compiler emits a backend-neutral representation with one timeline per link:

- `link_id` / slot id;
- `slot_type`;
- ParameterEpoch generation number;
- activation time and end time, when the next segment for that link is known;
- qdisc parameter groups for delay, rate, loss, queue, reorder, and
  routing-cost metadata.

The checked-in baseline is compact: it stores per-link segment counts, first and
last segment samples, field coverage, trace samples, and feasibility notes. The
full per-link segment table can be emitted with:

```bash
python satellite/apps/compile_parameter_timeline_to_qdisc.py \
  --run-dir satellite/output/starlink_shell_experiments/gz-z6metadata-2h-gated-20260611-0824 \
  --full-json-out /tmp/skyseed_parameter_qdisc_timeline_full.json
```

## Cadence

A ParameterEpoch generation is an event-time parameter update, not a fixed 10
second slice. The compiler records:

```text
fixed_10s_assumed=false
cadence_source=artifact ParameterEpoch event-time activation records
```

The current artifact has observed activation intervals ranging from 10 seconds
to 150 seconds. The presence of 10 second intervals in the artifact is treated
as input data, not as a backend invariant.

## Current Result

For the default artifact, the compact baseline reports:

```text
parameter_epoch_detail_records=275
parameter_epoch_summary_records=13
link_timelines=21
timeline_segments=3243
slot_type_counts: cross_plane_isl=3, gw_access=6, ut_access=12
```

Field coverage is complete for the parameters needed by a qdisc timeline:

```text
delay_ms=3243/3243
bandwidth_mbps=3243/3243
loss_percent=3243/3243
routing_cost=3243/3243
```

Queue limit and explicit reorder route/queue ids are not present in the current
ParameterEpoch artifact, so the qdisc timeline records those fields as absent
rather than inventing a policy.

## TheaterQ Feasibility

TheaterQ source is publicly accessible at:

```text
https://github.com/cs7org/TheaterQ
```

The repository describes TheaterQ as a Linux qdisc for dynamic link emulation.
It provides an out-of-tree kernel module and a tc/iproute2 plugin. The README
documents GPL-2.0 licensing, kernel-module build requirements, an iproute2 tc
plugin, CSV Trace Files, and syncgroups for synchronizing multiple qdisc
instances. The paper page is:

```text
https://arxiv.org/abs/2510.27057
```

Fit for Skyseed:

- Delay maps to TheaterQ `LATENCY`.
- Bandwidth maps to `RATE`.
- Loss maps to scaled `LOSS`.
- Queueing can map to `LIMIT` and extended `QUEUE_ID` when Skyseed has an
  explicit queue policy.
- Reordering can map to extended `ROUTE_ID` when a route-change-to-id policy is
  defined.
- Routing cost is Skyseed metadata and is not a direct qdisc field.
- Per-link timelines are a natural fit because TheaterQ ingests a trace per
  qdisc instance.
- Multi-qdisc synchronization is plausible via TheaterQ syncgroups, but large
  Skyseed topologies may require module parameter sizing beyond defaults.

Conclusion: TheaterQ is suitable for a future isolated demo investigation, but
it requires an external kernel module and tc plugin. This goal did not build or
load TheaterQ, and TheaterQ is not part of the canonical runtime path.

## Fallback Scaffold

Because this round does not load external modules, the compiler also emits a
fallback scaffold:

- TheaterQ extended CSV trace samples generated from the Skyseed timeline;
- userspace `tc qdisc change ... netem` command templates for comparison;
- explicit flags saying the scaffold does not run a kernel qdisc and does not
  claim TheaterQ timing fidelity.

The fallback is only a format and semantic validation aid. It is not the final
optimization and should not be mistaken for a custom qdisc implementation.

## Commands

Compile the compact baseline:

```bash
python satellite/apps/compile_parameter_timeline_to_qdisc.py --format text
```

Verification:

```bash
python -m py_compile satellite/apps/compile_parameter_timeline_to_qdisc.py
python -m unittest satellite/test_parameter_qdisc_timeline_compiler.py
git diff --check
```

## Next Step

This is ready to enter an isolated qdisc timeline demo or experimental
ParameterEpoch backend design. The next step should explicitly choose whether
to use TheaterQ in a disposable isolated environment or to keep using the
userspace/tc-change scaffold while a native timeline backend design matures.
