# r62 Follow-Up: H1 Preload Stabilization

Date: 2026-07-04
Status: r63 fail-closed; H0->H1 preload path partially validated; generation-key mismatch remains

## Goal

- Prepare a new 3H-240s run after r53/r55/r62 exposed artifact reuse, BPF-owned
  runtime-state drift, and H1 rolling-horizon preparation cost.
- Avoid spending another long run on issues that can be detected before launch.

## Findings

- r53/r55 preserved artifacts were from the old hardlink-contaminated lineage and
  must fail closed before reuse.
- BPF-owned operational clean skipped host `update_slot_parameters`, but
  downstream link-state metadata still needed to advance.
- r62 H1 maintenance was not a single generation taking 20 minutes; it was a
  full rolling-window preload of 25 future generations, with thousands of
  per-entry BPF map updates.
- `interface_role_map` was being treated like per-generation data even though
  role capability belongs to attach/role-union preparation.
- Report/audit layers still had old assumptions, including role-map no-op diff
  as a health signal and missing labels for the new preload path.
- Deferring all H1 generation preload would avoid the maintenance pause but
  would not be a complete correctness story unless something filled readiness
  before H1 consumed it.

## Changes

- Restored `CONTAMINATED_DO_NOT_REUSE.json` markers for preserved r53/r55
  artifacts.
- Added metadata-only link-state advancement for BPF-owned parameter skips.
- Made preactivation preload skip `interface_role_map` writes and report the
  skipped ownership explicitly.
- Added bounded parallel inactive-map writes for preactivation preload while
  preserving fail-closed behavior and deferred active-generation flip.
- Reconnected rolling H1 maintenance to produce real preloaded generation
  records, not only deferred records.
- Kept compact artifacts compact while preserving summaries for skipped role-map
  writes and parallel preload execution.
- Updated runtime/profile/audit reporting so labels and counts match the new
  path.

## Validation

- `python3 -m py_compile satellite/apps/run_starlink_shell_experiment.py satellite/bpf_mpls_live.py satellite/linux_mpls_runtime.py satellite/apps/run_skyseed_validation_profile.py`
- Focused tests covering BPF preload, runtime defaults, profile audit, and
  timeline queue behavior.
- Broad regression set:
  `Ran 428 tests ... OK`
- `git diff --check` on touched code and docs passed.

## Remaining

- The expected H0/H1 preload wall-time improvement is not yet measured in a real
  BPF run; the next 3H-240s run must validate it.
- No full ready-depth background worker yet. Current correctness relies on H1
  preloaded generation records being produced before H1 consumes them.
- No generation-keyed logical-state deduplication yet.
- No true bpftool batch-update interface yet; current speedup uses bounded
  parallel `bpftool map update` calls.

## Watch Next

- Compare bootstrap/pre-runtime generation preload cost with rolling H1
  maintenance cost.
- Confirm H1 has real preloaded generation records, not missing or stale
  readiness.
- Confirm active generation changes only after required inactive data writes
  succeed.
- Confirm reports/audits describe the current path rather than the old full
  serial preload path.
- Watch for repeated fallback to full reload, full attach, qdisc/rebind churn,
  or giant duplicated reports.

## r63 3H-240s Runtime Result

Run:
`satellite/output/validation_profiles/s4-full-h3-240s-runtime-20260704-r63`

Profile and source:

- Profile: `operational-clean-bpf-owned`.
- Command family: `run_starlink_shell_experiment.py --run-mode real-runtime
  --horizon-seconds 240 --continuous --loop-count 3 --forwarding-backend
  bpf_owned --host-prepare-policy off --preflight-policy require-clean`.
- Frozen source bundle:
  `satellite/output/validation_profiles/s4-full-h3-240s-prepare-20260704-r58/frozen_prepared_update_bundle.json`.
- r53/r55 preserved artifacts were not reused.

Outcome:

- `aggregate_runtime_succeeded=false`; the run stopped fail-closed at
  `H1:starlink-shell-slice-0001`.
- Failure:
  `bpf_transition_not_preloaded_flip_only: actual_horizon_generation_key=H1:generation-0,
  expected_horizon_generation_key=H1:generation-32,
  preloaded_generation_consumed=true,
  prepare_mode=preloaded_generation_flip_only`.
- Rollback completed and `post_run_preflight.json` reported
  `clean_after_run=true`. Compose containers and networks were clean after
  final cleanup.

What r63 proved:

- The run used the clean r58 frozen prepared bundle and did not reuse
  contaminated r53/r55 products.
- H0 preload improved versus r62 but is still expensive:
  `pre_runtime_generation_preload_wall_seconds=746.040024` versus r62 baseline
  `999.065015`.
- H1 rolling maintenance produced real BPF preloaded generations:
  `bpf_preloaded_generation_count=25`, `bpf_deferred_generation_count=0`,
  `synchronous_generation_preload_skipped=false`.
- H1 maintenance avoided full runtime reload and full attach:
  `bpf_preload_full_attach_count=0`,
  `bpf_preload_delta_attach_count=0`,
  `bpf_preload_runtime_full_generation_reload_count=0`; attach capability
  prepare used `delta_attach_count=31`, `full_attach_count=0`.
- The H0 runtime consumed the H1 preload path through
  `preloaded_generation_flip_only` for 24 epochs after the initial reused
  readiness epoch; no runtime fallback, Linux GZ fallback, qdisc update, or
  full-generation reload was observed.
- Reports now identify the blocking preload path as
  `bounded_parallel_preactivation_preload`, and role-map/report fields are
  present.

New blocker:

- The boundary from H0 to H1 reaches the first H1 epoch, but readiness mixes
  logical and physical generation-key semantics. The H1 preloaded record uses
  logical `H1:generation-0` and physical `H1:generation-32`; the runtime
  fail-closed assertion expected the active key derived from the physical
  active generation (`H1:generation-32`) while readiness still exposed
  `active_horizon_generation_key=H1:generation-0`.
- Before another expensive run, make generation key semantics explicit:
  either store separate logical and physical active horizon keys and compare
  the physical key at the BPF boundary, or make `active_horizon_generation_key`
  consistently physical for BPF-owned runtime readiness.
- The final `rolling_horizon_preloaded_bpf_generations.json` is overwritten by
  the latest rolling maintenance segment. After r63 it describes H2, not H1;
  audit tooling should preserve per-segment preload artifacts or make the
  overwrite explicit.

Additional observations:

- H1 maintenance wall time was `912.797201s`; this is below the previous
  20-minute serial stall but still a large boundary pause.
- H2 maintenance wall time was `1711.710496s`, with H0 cleanup active
  (`cleanup_previous_horizon_id=H0`) and `delta_attach_count=156`; this is the
  next performance/correctness area after the generation-key fix.
- Final cleanup took `640.100529s`, including
  `compose_down_after_wall_seconds=297.813575`; cleanup eventually succeeded
  and left the environment clean.
