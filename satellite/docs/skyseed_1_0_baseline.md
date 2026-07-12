# Skyseed Satellite Internet 1.0 Baseline

Status: validated on 2026-07-12

This is the durable checkpoint before the next module-splitting refactor. It
records the active architecture, the successful S4 validation flow, and the
commands needed to rebuild prepared inputs and run the experiment again. Large
runtime artifacts remain outside Git; their contracts, counts, and acceptance
evidence are recorded here and in the r141 round note.

## Validated Scope

- Profile: `rich_s4_operational_clean_bpf`
- Environment: Ubuntu 20.04, Linux 5.4 compatibility path, Python `seed310`
- Topology: S4, 348 satellites, 28 selected UTs, 12 gateways, 14 zones
- Runtime: four 240-second horizons, 25 epochs per horizon, 100 epochs total
- Dataplane: strict `bpf_owned`, shared G/Z, no Linux/MPLS/NAT/Mini-Internet
  forwarding fallback
- Rolling ownership: scheduler registers compact successor requests; workers
  expand referenced contexts, prepare future physical resources and attach
  capability, preload 25 inactive generations, and clean retired generations
- Observer: nonblocking 30-second ping observation to gateway service loopbacks

The validated real run was
`s4-1_0-compact-handoff-r141-long3-20260712`. It returned zero, completed
H0-H3 100/100, completed rollback, and left zero owned residue.

## Prepare A Fresh Frozen Bundle

The prepared bundle is not self-contained: it references horizon context
artifacts in its preview directory. Keep the entire preview directory until the
real run and offline replays finish. Do not copy only
`prepared_update_bundle.json`.

```bash
cd /home/seed/skyseed
export PY=/home/seed/miniconda3/envs/seed310/bin/python
export PYTHONPATH=.:satellite:satellite/apps
export RUN_ROOT=/tmp/skyseed_validation_profiles
export PREVIEW_ID="s4-1_0-prepare-$(date -u +%Y%m%dT%H%M%SZ)"

$PY - "$RUN_ROOT" "$PREVIEW_ID" <<'PY'
from pathlib import Path
import subprocess
import sys

from apps.run_skyseed_validation_profile import (
    build_underlying_command,
    load_profile,
    resolve_catalog_contract,
)

run_root = Path(sys.argv[1]).resolve()
run_id = sys.argv[2]
profile = load_profile("rich_s4_operational_clean_bpf")
gate = resolve_catalog_contract(profile)
command = build_underlying_command(
    profile=profile,
    catalog_gate=gate,
    run_root=run_root,
    run_id=run_id,
)
command[command.index("--run-mode") + 1] = "preview"
raise SystemExit(subprocess.run(command, cwd="/home/seed/skyseed").returncode)
PY

export BUNDLE="$RUN_ROOT/$PREVIEW_ID/prepared_update_bundle.json"
test -s "$BUNDLE"
```

The r141 preparation produced four 25-record context artifacts. Its bundle ID
was `prepared-bundle-60f6a69b5116fcd3`, with bundle hash
`60f6a69b5116fcd3332a6c959cd80c755993db9ba834bc1c286e15916cc3d6d9`.
These are evidence values, not hard-coded acceptance values; fresh preparation
may change them when inputs or code intentionally change.

## Cheap Gates Before Compose

```bash
cd /home/seed/skyseed
export PY=/home/seed/miniconda3/envs/seed310/bin/python
export PYTHONPATH=.:satellite:satellite/apps

$PY -m py_compile \
  satellite/apps/run_skyseed_validation_profile.py \
  satellite/apps/run_starlink_shell_experiment.py \
  satellite/apps/pre_long_run_contract_check.py \
  satellite/horizon_maintenance.py \
  satellite/horizon_maintenance_process_worker.py \
  satellite/linux_mpls_runtime.py

$PY -m unittest discover -s satellite -p 'test_*.py'

$PY satellite/apps/replay_prepared_runtime_consumer_contracts.py \
  --run-dir "$RUN_ROOT/$PREVIEW_ID" \
  --output "$RUN_ROOT/$PREVIEW_ID/prepared_runtime_consumer_replay.json"

$PY satellite/apps/replay_r134_shared_gz_generations.py \
  --run-dir "$RUN_ROOT/$PREVIEW_ID" \
  --output "$RUN_ROOT/$PREVIEW_ID/shared_gz_generation_replay.json"

$PY satellite/apps/skyseed_preflight.py --format json
```

Require four-horizon coverage, 25 entries per horizon, zero replay blockers, a
clean preflight, current BPF source compilation, and no stale prepared-bundle
mismatch. The frozen/live host-state alias must accept identical snapshot/epoch
identities and reject a genuinely stale snapshot or epoch.

## Run The Real Validation

```bash
cd /home/seed/skyseed
export PY=/home/seed/miniconda3/envs/seed310/bin/python
export PYTHONPATH=.:satellite:satellite/apps
export REAL_ID="s4-1_0-real-$(date -u +%Y%m%dT%H%M%SZ)"

$PY satellite/apps/run_skyseed_validation_profile.py \
  --profile rich_s4_operational_clean_bpf \
  --run-root "$RUN_ROOT" \
  --run-id "$REAL_ID" \
  --load-frozen-prepared-bundle "$BUNDLE" \
  --format json
```

Monitor it with machine-readable artifacts:

```bash
$PY satellite/apps/inspect_temporal_run.py "$RUN_ROOT/$REAL_ID"
```

Do not add a fixed whole-startup timeout. The validated run required 1145.86s
for Compose and 1947.29s from run start to first epoch execution. Stage failure,
resource deadlock, and actual no-progress evidence remain failures; elapsed
startup wall time by itself is evidence, not a kill gate.

## Acceptance Evidence

Require all of the following after exit:

- profile return code 0 and `aggregate_runtime_succeeded=true`
- 100 `runtime_epoch_result` records, H0-H3 25 each, no runtime exception
- three worker-ready and three ready-consumed events
- each worker has 25 inactive preloads, zero blockers, and no forbidden work
- H3 physical generation zero is consumed successfully
- runtime BPF miss/drop delta is zero; missing-generation, missing-label,
  role-map, unknown-role, manual-pop-failure, and Linux fallback counts are zero
- rollback completes and post-run preflight reports zero containers, networks,
  direct host interfaces, BPF pins, and runtime locks
- ping observations remain raw nonblocking evidence; isolated timeouts are
  preserved instead of repaired with a fallback

Post-run commands:

```bash
$PY satellite/apps/audit_runtime_ping_observer.py \
  "$RUN_ROOT/$REAL_ID/runtime_ping_observation.jsonl" \
  --output "$RUN_ROOT/$REAL_ID/runtime_ping_observer_audit.json"

$PY satellite/apps/skyseed_preflight.py --format json
```

The r141 baseline observed 27 valid pings: 25 successes and two isolated
timeouts on different UT/gateway/policy tuples. Both gateways had successes
elsewhere; there was no established fixed-gateway or same-flow consecutive
outage.

## Timing Baseline And Accepted Follow-ups

- Worker wall time: 120.71s, 93.47s, 99.30s
- Scheduler successor registration: 6.31s, 2.42s, 1.63s
- Runtime transition: average 7.48s, p95 12.67s, worst 18.15s
- Activation lag: average 24.13s, peak 48.08s, final 0.30s
- Final cleanup: 560.60s total, including 283.08s Compose down

The 90-second-class successor physical preparation is no longer on the epoch
critical path. Remaining scheduler registration amortized to at most 0.252s per
epoch and is acceptable for 1.0. About 1.5-3s per epoch remains incompletely
attributed outside current transition submetrics. Better timing attribution and
further externalization of frozen base/readiness payloads are post-1.0
optimization work, not new correctness gates.

## Refactor Rule

During the upcoming module split, preserve the behavior above before changing
performance or policy. A refactor checkpoint is not equivalent to this baseline
until the prepared producer, ordered consumer replay, all three horizon
boundaries, H3 generation zero, ping evidence, rollback, and zero-residue
postflight still agree.
