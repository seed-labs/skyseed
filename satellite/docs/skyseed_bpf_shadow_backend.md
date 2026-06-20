# Skyseed Experimental BPF Shadow Backend

Goal 14 lands an experimental BPF optimized backend behind the Skyseed backend
abstraction as a shadow-only compiler and auditor. It does not replace the
canonical Linux/direct runtime and it does not take over the real dataplane.

## Scope

The backend mode is `experimental_bpf_shadow`.

The compiler reads existing Skyseed runtime artifacts and emits a
`BackendCompilePlan` plus `BackendAuditReport` shaped report containing:

- G/Z FEC generation maps.
- ParameterEpoch BPF parameter maps.
- fail-closed rules.
- runtime and parameter counter schemas.
- compact summary fields for regression and review.

The implementation is intentionally shadow-only:

- `canonical_runtime_changed=false`
- `linux_direct_backend_replaced=false`
- `real_dataplane_touched=false`
- `qdisc_timeline_landing=false`
- `theaterq_or_external_qdisc_module=false`

## Inputs

`existing_artifact` mode reads an existing run directory, such as the 2h gated
artifact:

```bash
python satellite/apps/compile_skyseed_bpf_shadow_backend.py \
  --run-dir satellite/output/starlink_shell_experiments/gz-z6metadata-2h-gated-20260611-0824 \
  --format text
```

`short_runtime_shadow` mode uses the same artifact contract for a freshly
produced bounded runtime run directory. The canonical runtime still produces
the normal artifacts; this compiler reads them afterward or as a sidecar step
and writes BPF shadow artifacts without touching forwarding:

```bash
python satellite/apps/compile_skyseed_bpf_shadow_backend.py \
  --input-mode short_runtime_shadow \
  --run-dir <short-runtime-run-dir> \
  --shadow-artifact-out <short-runtime-run-dir>/bpf_shadow_backend.json \
  --format text
```

Do not commit generated files under `satellite/output`.

## Real-Runtime Shadow Emission

Bounded real-runtime runs can emit the same shadow artifacts directly by
passing `--emit-bpf-shadow` to `run_starlink_shell_experiment.py`:

```bash
python satellite/apps/run_starlink_shell_experiment.py \
  --run-mode real-runtime \
  --emit-bpf-shadow \
  <normal short-run arguments>
```

This switch runs after canonical runtime artifacts have been written. It reads
the run directory and writes:

- `bpf_shadow_backend_summary.json`
- `bpf_shadow_backend_consistency.json`

The switch does not change apply, forwarding, validation, rollback, or
post-run preflight behavior. The canonical Linux/direct runtime remains the
only real dataplane. BPF shadow emission is a consistency sanity artifact, not
an optimized backend replacement.

The consistency report compares the shadow summary with canonical artifacts:

- G/Z FEC plan and diff counts.
- ParameterEpoch execution detail generations, links, and parameter entries.
- backend audit pass/fail.
- `canonical_runtime_unchanged=true`.
- `fixed_10s_assumed=false`.

## Generation And Cadence

The compiler does not assume a fixed 10s slice. Runtime generation metadata is
read from the G/Z FEC plan and `temporal_plan.runtime_epochs`. Parameter
generation metadata is read from `epoch_execution_details.jsonl` and
ParameterEpoch metadata in `temporal_plan.json` or
`parameter_epoch_summary.json`.

The compact report always carries:

- `fixed_10s_assumed=false`
- `qdisc_timeline_backend_status=blocked_or_trace_only`
- `canonical_runtime_unchanged=true`

## BPF Map Model

Runtime maps:

- `active_generation`: active runtime generation id.
- `generation_status`: installed and active metadata per generation.
- `fec_forwarding`: forwarding/drop entries keyed by generation and FEC id.
- runtime counters for generation hits, FEC hits, drops, local delivery, and
  future generation probes.

Parameter maps:

- `parameter_generation`: active ParameterEpoch generation id.
- `param_status`: installed and active metadata per parameter generation.
- `parameter_values`: active/loss/mark values keyed by generation, link, and
  flow class.
- `parameter_counters`: generation hits, entry hits, pass/drop counters.
- `future_param_gen`: trace-only preloaded future generation ids.

## Audit Rules

The backend audit checks:

- G/Z FEC intent matches the BPF generation map.
- ParameterEpoch active/loss/drop intent matches the BPF parameter map.
- missing runtime generation, missing FEC, inactive FEC tombstone, missing
  parameter generation, missing parameter, and inactive parameter tombstone all
  fail closed.
- future runtime and parameter generations are not active early.
- Linux/MPLS labels and LFIB estimates are recorded as leakage only and are not
  used as BPF intent sources.

The qdisc timeline remains blocked or trace-only because TheaterQ requires an
external kernel module and tc plugin. Goal 14 does not continue TheaterQ or
land a qdisc timeline backend.

## Dataplane Canary

Goal 16 adds a separate opt-in mode:

```bash
python satellite/apps/run_starlink_shell_experiment.py \
  --run-mode real-runtime \
  --bpf-dataplane-canary \
  <normal short-run arguments>
```

The default is still off. Without `--bpf-dataplane-canary`, the canonical
Linux/direct runtime, qdisc/netem ParameterEpoch handling, rollback, and
post-run preflight behavior are unchanged.

The canary is not a full BPF replacement. It attaches one tc-BPF classifier to
one live UT access host interface after the bounded runtime has produced a
successful UT ping and before compose/direct-backend cleanup starts. The
classifier matches only that selected canary target IP. Matching packets use:

- `active_generation` plus `generation_status`.
- one loaded `fec_forwarding` entry for the selected canary UT/gateway path.
- `parameter_generation`, `param_status`, and one `parameter_values` entry for
  active/loss/drop/mark decisions.

All non-canary traffic falls through with `TC_ACT_OK` and remains on the
canonical Linux/direct backend. Parameter delay, rate, and bandwidth continue
to use the existing qdisc/netem path; the BPF canary handles only active,
loss/drop, mark, and counters. The qdisc timeline remains
`blocked_or_trace_only`; no TheaterQ or custom qdisc module is loaded.

The canary writes `bpf_dataplane_canary.json` with these metrics:

- `bpf_canary_enabled`
- `bpf_attached_interface_count`
- `bpf_loaded_generation_count`
- `bpf_loaded_fec_count`
- `bpf_pass_counters`
- `bpf_drop_counters`
- `bpf_redirect_counters`
- `canonical_fallback_count`
- `bpf_rollback_completed`

Rollback runs in a `finally` path and removes the canary clsact attachment plus
the pinned canary maps under `/sys/fs/bpf/tc/globals`. The normal post-run
preflight still runs afterward and must report clean for the run to be accepted.
