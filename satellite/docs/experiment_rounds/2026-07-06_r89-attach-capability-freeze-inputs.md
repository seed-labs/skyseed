# r89: Attach Capability Consumes Frozen Inputs

Date: 2026-07-06
Status: fixed; pending operational validation

## Goal

- Treat attach-capability prepare as a semantic contract fix, not a timing-only
  optimization.
- Ensure maintenance attach capability consumes frozen prepared inputs instead
  of doing its own broad live interface discovery/logical binding.
- Preserve fail-closed behavior and keep active/visible state changes at the
  runtime boundary only.

## Findings

- The preload worker already consumed frozen compiled runtime inputs and reused
  prepared attach capability.
- The maintenance attach-capability step ran before frozen inputs were created,
  so it could still re-enter compile/discovery/binding work.
- Even a live search that later skips work is the wrong ownership boundary for
  this stage; the maintenance prepare should consume a frozen manifest.

## Changes

- Moved rolling-horizon frozen compiled input generation ahead of attach
  capability prepare.
- Added frozen compiled runtime input handoff to
  `_prepare_bpf_owned_preactivation_attach_capability`.
- When frozen inputs are present, attach-capability prepare now derives its
  interface scope from the manifest and does not call the host-interface lookup.
- For multi-generation maintenance segments, attach capability receives a union
  manifest built from all frozen records, while preload work items still receive
  their per-generation frozen manifests.
- Extended compact attach prepare records with input/source readiness fields.

## Validation

- Passed compile checks:
  `python -m py_compile satellite/linux_mpls_runtime.py satellite/apps/run_starlink_shell_experiment.py satellite/test_runtime_validation_defaults.py`
- Passed focused and adjacent tests:
  - `python -m unittest satellite.test_runtime_validation_defaults`
  - `python -m unittest satellite.test_bpf_mpls_live`
- Passed whitespace check:
  `git diff --check -- satellite/linux_mpls_runtime.py satellite/apps/run_starlink_shell_experiment.py satellite/test_runtime_validation_defaults.py`
- Session check reports `operational_iteration_ready=True` and suppresses
  legacy host MPLS / bridge-netfilter warnings for the current BPF-owned
  operational iteration.

## Remaining

- Validate in the next 3H/240s operational iteration that attach capability
  reports `attach_capability_prepare_inputs_source=queued_horizon_maintenance_frozen_manifest`.
- Confirm attach prepare timing no longer carries broad
  `interface_discovery_logical_binding_wall_seconds`.
- Continue with qdisc batch executor and maintenance processization after this
  semantic fix is accepted.
