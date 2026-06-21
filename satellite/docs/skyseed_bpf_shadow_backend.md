# Skyseed Experimental BPF Backend

Goal 14 landed an experimental BPF optimized backend behind the Skyseed
backend abstraction as a shadow-only compiler and auditor. Goal 17 extends
that work into an opt-in experimental backend apply path:

```text
Skyseed semantic intent
  -> BPF backend compile plan
  -> BPF map/generation load
  -> opt-in dataplane apply
  -> audit / fallback / rollback
```

This is not a full replacement for the canonical Linux/direct runtime. The
default remains BPF off. The canonical runtime, qdisc/netem ParameterEpoch
handling, rollback, and post-run preflight behavior are unchanged unless a run
explicitly opts in to the experimental BPF backend.

## Scope

The shadow compiler mode is `experimental_bpf_shadow`. The opt-in live backend
mode is `experimental_bpf_backend`.

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

Goal 16 added a separate opt-in dataplane canary. Goal 17 keeps the narrow
dataplane safety model but removes the hard-coded canary dependency: the
scope is selected from Skyseed semantic intent plus an explicit selector, not
from a fixed UT/GW/interface.

New runs should use:

```bash
python satellite/apps/run_starlink_shell_experiment.py \
  --run-mode real-runtime \
  --bpf-backend experimental \
  --bpf-selector-ut ut3 \
  --bpf-selector-gw gw5 \
  --bpf-max-scope-entries 1 \
  <normal short-run arguments>
```

The legacy Goal 16 flag still maps to the same experimental backend path:

```bash
python satellite/apps/run_starlink_shell_experiment.py \
  --run-mode real-runtime \
  --bpf-dataplane-canary \
  <normal short-run arguments>
```

The default is still off. Without `--bpf-backend experimental` or the legacy
`--bpf-dataplane-canary`, the canonical Linux/direct runtime is unchanged.

The experimental backend is not a full BPF replacement. It attaches tc-BPF only
to a bounded canary subset after the bounded runtime has produced a successful
intent-matched UT ping and before compose/direct-backend cleanup starts. The
classifier matches only the selected target IPs loaded into BPF maps. Matching
packets use:

- `active_generation` plus `generation_status`.
- loaded `fec_forwarding` entries for the selected semantic FEC scope.
- `parameter_generation`, `param_status`, and one `parameter_values` entry for
  active/loss/drop/mark decisions.

Scope can be constrained by:

- UT id: `--bpf-selector-ut`
- gateway id: `--bpf-selector-gw`
- G/Z FEC id: `--bpf-selector-fec`
- zone id: `--bpf-selector-zone`
- RuntimeEpoch generation id: `--bpf-selector-generation`
- ParameterEpoch generation id: `--bpf-selector-parameter-generation`
- scale limit: `--bpf-max-scope-entries`

This keeps accidental full-network takeover fail-safe. The runner defaults to
`max_scope_entries=1`; the BPF maps also remain bounded for the experimental
path.

All non-selected traffic falls through with `TC_ACT_OK` and remains on the
canonical Linux/direct backend. Parameter delay, rate, and bandwidth continue
to use the existing qdisc/netem path; BPF handles only active, loss/drop, mark,
and counters. The qdisc timeline remains
`blocked_or_trace_only`; no TheaterQ or custom qdisc module is loaded.

The backend writes `bpf_experimental_backend.json` and a legacy
`bpf_dataplane_canary.json` alias with these audit fields:

- `bpf_backend_mode`
- `bpf_enabled`
- `bpf_scope`
- `loaded_runtime_generation_count`
- `loaded_gz_fec_count`
- `loaded_parameter_generation_count`
- `loaded_parameter_link_count`
- `active_generation_switch_count`
- `bpf_pass_counters`
- `bpf_drop_counters`
- `bpf_redirect_counters`
- `canonical_fallback_count`
- `bpf_fail_closed_counters`
- `canonical_runtime_unchanged`
- `bpf_rollback_completed`

Rollback runs in a `finally` path and removes the canary clsact attachment plus
the pinned canary maps under `/sys/fs/bpf/tc/globals`. The normal post-run
preflight still runs afterward and must report clean for the run to be accepted.

## Goal 17 Status

Goal 17 is still experimental backend landing, not S5 A/B validation and not
a realistic workload replacement. The next step after this landing is short
S5 A/B and realistic workload work using the same opt-in selector and audit
contract.
