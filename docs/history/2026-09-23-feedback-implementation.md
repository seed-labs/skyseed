# Feedback round implementation — 2026-09-23

**Review status update — 23 September 2026:** the user accepted the completed
independent review of this Feedback work, its subsequent repairs and the naming/
documentation closeout, and authorized commit and publication. See the
[accepted release closeout](2026-09-23-naming-migration.md#accepted-release-closeout--2026-09-23).
The sections below preserve the original implementation-stage results, source names
and validation limits; publication adds no new live or visual acceptance.

This records the implementation working tree, not independent acceptance, live
qualification, or authorization to commit. The task implements shared etcd
interaction, unified controls, future material revision/continuation, and the
fixed-communication Traffic reset. The independent-review repair sections below
record five earlier gaps and the subsequent structural pass.
The verification limits below remain explicit.

## Completed behavior and Architecture Delta

- **Public interaction:** `InteractionClient`, `RequestReceipt` and
  `InteractionUnavailable` are exported by `skyseed_v2.experiment`. Independent
  programs can read focused owner facts, submit/query requests, exchange versioned
  JSON values and watch changes. Controller convenience controls now return
  receipts; acknowledgement does not mean execution or cleanup finished.
- **Ownership and service:** construction is inert. Explicit generation starts
  an output-scoped etcd member or attaches a configured external endpoint. The
  owned member survives computation closure. Explicit stop excludes live owners,
  startup and cleanup; it signals only the recorded owned process. Restart
  restores records, not Model memory or kernel state. Native etcd Unix listening
  plus generated mounts makes the same API reachable from isolated namespaces.
- **Control path:** the per-owner Unix RPC transport is removed. CLI/Python
  controls, runtime claim/release, shutdown and computation-failure coordination
  use etcd. Owner consumers call local primitives; local error/signal cleanup
  remains independent of connectivity. Requests carry session identity,
  validation, deferral, progress, effective positions and terminal outcomes.
  Reset fences old sessions. Admission races compare the owner publication;
  response loss does not re-execute an effect within the live consumer.
- **Scheduled changes:** pause completes the aligned boundary and holds later
  activation. Resume supersedes an outstanding pause. Delivery duration changes
  at the next delivery, preparation lead at the next decision, and resource
  sizing only when relevant resources are next created. Immutable experiment
  structure remains outside parameter controls.
- **Future revision:** Producer retains explicit reusable framework continuation.
  Model instances/private state and user values remain author-owned. Complete
  candidates replace the affected suffix; Runner serializes adoption with entry
  into PREPARING. A lost race rebases on the selected predecessor and recomputes
  descendants. Failure before selection preserves the valid chain; failure
  after selection retains the compatible adopted head and reports that adoption.
  Generic Planner and Runner have no etcd/orchestration dependency.
- **Records and files:** etcd separates owner publications, requests/results,
  user values and coordination. Large inspections and adoption proposals use
  file references. `results/compiler/selection.json` selects versions;
  `results/revisions/` retains candidates/evidence. Completed publications pin
  selected evidence, while activation records identify actual execution.
- **Traffic:** `workload.py` replaces the route-derived planner. Static GS/Zone
  pairs, direction, interval and duration define persistent communications.
  Runtime ACTIVE bindings/service assignments control source relocation.
  Pause alone keeps a valid sender; missing sources are recorded and retried.
  Reset/stop drains owned senders before runtime resources. The old per-delivery
  workload admission, route qualification, coverage scoring and conformance
  presentation paths are removed. Globe/timeline playback and export retain
  actual events and supported ingress hops, including uncertainty and gaps.

Precise contracts and examples remain in
[experiment control](../development/experiment-control.md),
[the control/setup guide](../user-guides/computation-and-controls.md),
[Compiler/Runner](../development/compiler-and-runner.md), and the
[Traffic generation](../../tools/generation/traffic/README.md) and
[observation](../../tools/observation/traffic/README.md) READMEs.
The S4 example uses the simplified workload configuration. No research algorithm,
substantial new Example or recovery platform was added.

## Verification and limits

Verification used the existing Conda environment:
`/home/lionheart/miniconda3/envs/skyseed_dev/bin/python`, Python 3.12.13.
Real temporary etcd instances exercised separate host processes, user values and
watch/version behavior, control/result delivery, reset isolation, service outage,
explicit persistence/restart, admission/closure races and lost acknowledgements.
The native Unix etcd endpoint was exercised from the provisioned client API on
the host; generated Compose environment/module/socket mounts were inspected.

Small BPF/Linux scenes cover author-driven feedback reads, candidate failure,
actual Runner PREPARING arbitration and late rebase, independent-process adoption,
selected/pinned history, extension after revision, future parameters, and
compatible continuation after an adopted candidate's evidence-close failure.
Real backend adapters prepare/activate revised material against offline kernel
fixtures. Lifecycle tests use real child processes for readiness/cancellation.

Final check outcomes are recorded with commands in the evidence directory below.
The observation suite passed 128 tests with 13 skips. The control/continuation
set passed 14 tests. The broader affected run passed 186 tests before two
publication/reset-wait assumptions failed; corrected tests and the remaining
affected cases then passed **66 tests with 5 skips**. The final revision-only
run passed **5 tests**, including file-referenced multiprocess adoption and
post-adoption error handling. These counts overlap;
they are not a full-suite pass claim. Collection found 1,748 tests.

Firefox fixture checks cover communication selection, play/pause, seek, event
selection, globe reset, supported arrivals, and absence of invented gap/aggregate
hops. Both new Mermaid diagrams render with the pinned 10.9.1 renderer. Browser
screenshots are fixture evidence, not live packet/network validation.

Import dependency analysis, compilation, dependency consistency and whitespace
checks passed. Link/fence validation passed for **103 application documents**,
including this report. The unmodified full/focused checker also scans incoming
saved research websites and fails on their missing website assets; that unrelated
test is explicitly excluded from the final affected regression command. Research
files were not edited to make this check pass.

No full satellite Docker/BPF experiment, host-network change, container-image
build, real Linux/BPF kernel qualification or external-etcd deployment was run.
Linux still lacks complete intermediate per-packet transit evidence. No crash
recovery or exactly-once external effects are claimed. Independent Work review
and live qualification remain separate activities.

## Setup and review evidence

Declared and installed only `etcd3gw` 2.7.0 and `requests-unixsocket` 0.4.1 with
their required small dependencies in the existing environment. The setup helper
downloads and verifies official etcd 3.6.5 release checksums; `etcd`/`etcdctl` are
installed under `/home/lionheart/.local/share/skyseed/etcd`. No global daemon or
unrelated environment upgrade was performed. Host setup is complete here.

Before deploying container programs, provide Python 3.10+ and the generated
client requirements in the selected image. The default native socket mount
assumes Docker on the output host and socket access for the program. External
etcd needs an explicitly reachable container endpoint. Regenerate old outputs
for managed use of the new control contract; historical evidence stays readable.
After owner/deployment cleanup, stop the owned service explicitly when results
are no longer needed.

All temporary services used by the completed checks were drained. One failed
test left a runtime fixture alive after its cleanup assertion failed; its current
session accepted `cancel-runtime` and confirmed closure. No arbitrary forced
termination was used.

Incoming baseline and review evidence:
`/home/lionheart/workspace/skyseed-local/implementation-baselines/feedback-20260922T153436Z/`.
`skyseed/` and `seed-emulator/` contain incoming status, diff, HEAD, branch and
file hashes; `preservation.json` records the comparison. `checks/` contains
commands and logs, `browser/` contains fixtures/screenshots, and `diagrams/`
contains rendered diagrams. `feedback-only.diff` excludes incoming work;
`feedback-files.json` lists the task files and hashes. Intermediate failures
remain in the evidence. The updated system SVG also passed browser card-bound
checks and visual inspection.

SkySEED remains on `feature/skyseed-v2` at
`e7a7c9bb53655fe85b5eb6c4ba2398a04ef0a7c1`. All 30 incoming modified/untracked
paths are unchanged. The separate SEED checkout's baseline files, status, HEAD
and index are unchanged. Accepted output directories were not targeted by any
write. Nothing was staged, committed or pushed.

## Independent-review repairs — 2026-09-23

The first independent review found five gaps in the implementation above. This
section records their implementation repair and regression evidence; it does not
claim independent acceptance. The repair baseline is separate from the original
round:
`/home/lionheart/workspace/skyseed-local/implementation-baselines/feedback-review-20260922T172614Z/`.

1. **Acceptance order:** pending controls sort by the etcd creation revision of
   their acceptance transaction. Request IDs and duplicate submissions cannot
   reorder effects. An Event-gated real-etcd consumer receives a future command,
   `pause/z-first`, `resume/a-second`, and a duplicate before consumption; eligible
   effects remain pause then resume, the pause expires as superseded, and the
   future request does not block either effect.
2. **Offline/runtime handoff:** runtime constructs its selected catalog inside
   `claim_runtime`. Offline adoption uses that same exclusive claim. Deterministic
   tests exercise both orderings through the generated runtime entry, the real
   catalog/adoption path and the first actual Runner artifact reader. Both BPF
   and Linux load the selected initial reference. Catalog refresh remains an
   incremental read of appended records; startup does not repeatedly scan history.
3. **Cancellation:** the Producer stop event reaches adoption's pre-attempt
   waits. Local cancellation restores the valid head and drains candidate/evidence
   owners even while all store calls remain unavailable. A separate recovery case
   proceeds normally. The submission-attempt flag is set before calling the store:
   cancellation after an accepted request with a lost acknowledgement waits for
   the authoritative result, applies the effect once, and retains the adopted
   compiler continuation. The existing post-adoption evidence-close failure case
   continues to verify compatible later extension.
4. **Initial delivery:** Producer captures framework continuation before priming;
   initial compiler construction and the entire dependent suffix can be replayed.
   Delivery zero has no predecessor. Runner freezes its initial reference under
   its condition when `prepare()` begins, and swaps any registered successor when
   an initial replacement wins. Tests cover single/multiple deliveries, repeated
   revision with nonzero acquisition prehistory, both backend adapters against
   offline kernel fixtures, and preparation winning with later rebase or explicit
   no-range rejection. Selected initial headers are checked against fixed generated
   scene/backend/Timeline/cadence inputs; startup binds and loads the selected
   reference. Original materials and execution-definition provenance are retained.
5. **Model-owned caches:** generic Planner/revision restoration no longer imports
   concrete Models or edits their caches. RetainFirst rebuilds its selection
   shortcut for earlier/repeated timestamps. Geometric prediction reuses compatible
   forward proofs and rebuilds other ranges, including reduced tail coverage, on
   its existing geometry core. Repeated/reversed inputs are compared with fresh
   Model results; prior proof values remain immutable and normal shutdown releases
   the core. Custom Model private call history/user reads remain live while adopted
   framework parameter memory and acquisition inputs are restored. Failed candidate
   restoration and late rebase remain covered without new Model hooks.

The adjacent initial-successor selection and initial-reference verification paths
were repaired with delivery zero. A stale detached-history test expected bare
filenames although the existing writer records domain-relative paths; its assertions
now verify the actual files, sizes, hashes and record counts. Resource inventory also
found that the SIGINT subprocess fixtures left their intentionally persistent etcd
members outside the parent fixture's tracking. Those fixtures now explicitly stop
their member after child/owner exit; the three earlier members were drained through
the normal service API after confirming closure. No Traffic policy or
ACTIVE-runtime-fact semantics changed.

### Repair Architecture Delta

No new public methods/types, Model protocols, dependencies, control channels or
example defaults. The existing `frozen_through` status now reports `-1` before actual
initial preparation. Existing selection records can select delivery zero, whose
predecessor version is null. A private initial-reference verifier belongs to the
material-reference module; execution supplies the generated input contract. Runtime
ownership acquisition precedes catalog construction, and generic continuation
restores only framework state. Etcd interaction, actual-owner decisions, author-owned
feedback meaning and normal runtime progression remain the agreed architecture.
The blanket B1+ restriction has been removed from the contract and guide.

### Repair verification and preservation

Used `/home/lionheart/miniconda3/envs/skyseed_dev/bin/python`, Python 3.12.13,
Conda prefix `/home/lionheart/miniconda3/envs/skyseed_dev` (`skyseed_dev`). No environment
or dependency installation was needed. Commands below ran from `skyseed/skyseed_v2`;
`PY` denotes that absolute interpreter.

```bash
"$PY" -m pytest tests/integration/test_feedback_revision.py \
  tests/unit/test_experiment_control.py \
  tests/unit/test_producer_computation_optimization.py -q
# 38 passed, including acquisition prehistory and direct cache replay checks.

"$PY" -m pytest tests/unit/test_runner_batches.py \
  tests/unit/test_retained_computation.py tests/unit/test_realization_producer.py \
  tests/unit/test_dataplane_preparation.py tests/integration/test_bpf_execution_bootstrap.py \
  tests/integration/test_retained_workflow.py tests/integration/test_completed_ranges.py \
  tests/integration/test_shutdown_handoff.py tests/integration/test_closing_review.py \
  tests/integration/test_initial_readiness.py tests/integration/test_preparation_function.py \
  tests/integration/test_continuous_progression.py -q
# Diagnostic run: 135 passed, 2 failed; superseding checks below.

"$PY" -m pytest tests/integration/test_feedback_revision.py \
  tests/unit/test_realization_producer.py tests/integration/test_shutdown_handoff.py -q
# 53 passed; final serial run, including both previously failing cases.

"$PY" -m pytest tests/integration/test_closing_review.py -k sigint -q
# 3 passed, 2 deselected; child-owned member cleanup verified.

"$PY" scripts/check_dev_environment.py
"$PY" scripts/check_import_dependencies.py
"$PY" -m pip check
"$PY" -m compileall -q src/skyseed_v2 tests
# Existing documentation checker, application scope excluding incoming research downloads:
"$PY" /home/lionheart/workspace/skyseed-local/implementation-baselines/feedback-review-20260922T172614Z/checks/check_application_docs.py
git diff --check
```

Environment verification used command-local `CONDA_DEFAULT_ENV=skyseed_dev` and
`CONDA_PREFIX` matching the interpreter. Import dependencies, environment,
dependency consistency, compilation, whitespace and application documentation
checks pass; the latter covers 118 documents. Full documentation checking still has
the incoming saved research-site asset issue documented above; those files were
preserved, and that unrelated diagnostic was not repeatedly run. No diagrams were
edited or rendered in this repair.

`checks/feedback-review-first-tests.log` preserves the early fixture-signature and
same-time cache-reuse assertion failures; `feedback-review-regressions-final.log`
supersedes them. The initial import cycle diagnostic is retained in
`checks/tool-checks.json`; `feedback-review-imports-final.log` confirms its removal.
`feedback-review-lifecycle.log` preserves the stale evidence-path assertion and an
etcd HTTP read timeout during concurrent test runs. The latter's owned server log,
`shutdown-timeout-etcd.log`, records slow apply transactions (about 1.6–1.9 seconds).
The final serial affected run supersedes both failures without changing the client
timeout or assuming an unacknowledged write failed. The additional selected-input
negative checks and module relocation passed an intermediate 8-case initial/startup
run, followed by the final affected run. Test counts overlap and do not constitute
a full-suite qualification. The broader run also reports the existing Python
multithreaded-fork warning in completed-range fixtures.

The repair diff touches 18 existing files. `repair-only.diff` compares them with the
captured incoming source, and `repair-files.json` records before/after hashes.
`preservation.json` confirms that the other 1,097 captured SkySEED files and all
1,817 captured SEED files are unchanged, with no missing baseline files. Research,
navigation, diagrams, existing uncommitted implementation and unrelated work are
preserved outside those focused repair files. Both repositories retain their HEAD,
branch and staged diff; SEED status is unchanged. Accepted output directories were
not targeted; generated test outputs stayed under pytest's temporary roots. No
staging, commit, push, merge, package rename or publication work was performed.
Temporary fixture services and processes have drained. The outage-cancellation
fixture reconciles its stale publication only after proving local closure without
connectivity, allowing the test-owned service to stop normally.

These are implementation checks using small real-etcd fixtures and offline backend
adapters. No live Docker/container/kernel experiment, full satellite deployment,
privileged host change or new global daemon was run. Store outage after an adoption
attempt can still leave completion indeterminate until connectivity returns; no
arbitrary timeout, forced termination, crash recovery or exactly-once external
side-effect guarantee is added. Independent acceptance and live qualification remain
outstanding activities.

## Structural repair pass — 2026-09-23

The five-fix review above did not accept the whole Feedback round. This pass addresses
the subsequent structural review and is **implementation completion for another
independent review**, not whole-round acceptance or live Linux/BPF qualification.

### Resulting ownership and API changes

- Producer owns recording resources, sink health and diagnostics as one ordinary or
  candidate scope. Failed/rejected candidate acquisition, recording or publication
  cannot poison continuation or clear a pre-existing ordinary failure. Partial
  writer acquisition drains resources; cleanup preserves the primary error. Revision
  results carry `evidence_attempts` and secondary error notes; Producer status labels
  `evidence_scope`. Adopted continuation remains authoritative through later failure.
  Missing selected candidate evidence is reported explicitly, never replaced with
  superseded original observations.
- Catalog reference/version capture and final selection are short locked operations.
  Immutable header reads, suffix validation and selection serialization/staging run
  outside catalog/Runner delivery locks. Normal registration also reads first and
  rechecks selection before enqueueing. Runner's actual preparation decision still
  freezes a window. No selection-cleanup I/O follows a successful atomic rename.
- Unchanged request updates perform no transaction. Meaningful state and immutable
  history commit together; a separate small liveness key refreshes idle health.
  Public `state_history()` and `watch_state()` expose changes without requiring a
  control request, with session, sequence, observation time and store revision.
  `state()` reads state and heartbeat in one consistent transaction. Publication
  retries reuse an immutable identity after lost acknowledgements.
- Newly started/restarted owned etcd uses periodic one-hour MVCC retention, recorded
  in discovery. External attachment does not configure the shared service. Public
  `InteractionHistoryCompacted` distinguishes unavailable old revisions from an
  outage or no observed change. Meaningful history/request records and current user
  values have no automatic expiry; compaction is not a total-storage bound.
- The existing experiment consumer passes available runtime freeze facts to Producer.
  Producer performs no opportunistic network query and prunes only an advanced
  frontier; stale/unavailable facts conservatively retain checkpoints. Adoption
  still consults its real authority. The write-only `coordination/runtime` record
  is removed. `ExperimentController.status()` remains explicitly local diagnostics;
  shared interaction state owns version/session/freshness semantics.
- Traffic sender evidence v3 replaces sequence sets and RTT lists with bounded
  counters and online RTT aggregates. Terminal unique totals require a real ping
  summary; incomplete totals remain unknown. Replay uses detailed reply observations,
  handles duplicate/wrap/migration ambiguity, and retains only the necessary
  historical terminal-list fallback. Reader/facade/CLI/generated-script/aggregate
  inputs now match actual consumption; CLI `--experiment` explicitly locates the
  pinned completion. The kwargs-discarding projection alias and unused helpers are
  removed. Historical evidence stays readable through the simplified inputs.

No new owner, Model hook, dependency, feedback algorithm or Example was added.
The compiler continuation helper was reviewed and retained: its explicit field/type
whitelist copies framework material accumulators for independent forks, excludes
writer resources and Models, and keeps authored immutable program selection by
reference. Existing continuation/Model-cache tests cover its guarantees. Generic
Planner/revision code still does not identify concrete Model implementations.

Canonical contracts were updated in the linked developer/API/guide and Traffic
READMEs. Presentation rendering code and existing browser evidence remain applicable;
this pass makes no new visual-acceptance claim.

### Focused evidence

The existing `/home/lionheart/miniconda3/envs/skyseed_dev/bin/python` was used throughout:
Python 3.12.13, Conda prefix `/home/lionheart/miniconda3/envs/skyseed_dev` (`skyseed_dev`).
The activated environment check passes. An initial invocation had the correct absolute
interpreter but unset Conda shell labels; both diagnostic and corrected logs are kept.
No packages or service binaries were installed or upgraded.

Evidence root:
`/home/lionheart/workspace/skyseed-local/implementation-baselines/feedback-structural-20260922T182553Z/`.
`validation/results.json` records commands/scopes and raw log names. Principal results
(overlapping suites, not a full-suite total):

| Raw log in `validation/` | Result and scope |
|---|---|
| `final-core.log` | 56 passed: all previous revision behaviors, added candidate isolation/registration/pruning races, real-etcd controls, writer acquisition/continuation |
| `final-tools-lifecycle.log` | 41 passed: bounded sender state, recorded association, detached readers/Tools, completion pinning, corrected lifecycle cases |
| `final-interface-scope.log` | 20 passed: final consistent state reads, liveness/history/compaction/session tests, actual detached CLI, missing evidence and adopted-error scope |
| `final-detached-entry.log` | 7 passed: final detached Tool entry points and explicit module-fixture service teardown |
| `structural-evidence-catalog.log` | 6 passed: rejected/failed physical evidence followed by readable normal continuation, preserved original failure, delivery during held validation and preparation winning |
| `idle-measurement.json` | Real etcd: over 1.406 seconds, state revision stayed 2, deferred-request revision stayed 4, request history stayed at two entries, state history stayed at one; heartbeat advanced 2→5 and availability stayed true |

That idle database grew from 20,480 to 24,576 bytes for the small heartbeat write; this
is a specific smoke measurement, not a production growth estimate. Compaction tests
explicitly compact a temporary real member and verify failed old reads/watches plus
preserved current values, requests and state-history records. They also verify owned
startup flags and that external attachment does not start/configure a server.

The broader affected diagnostic run passed 102 tests and failed six: two fixtures
submitted before shared startup publication, two Tool expectations predated the
Traffic reset/module imports, one etcd transaction timed out, and the known saved
research-website link scan failed. Corrected fixtures and the timed-out lifecycle case
passed the focused rerun. Timeout semantics were not weakened. The first core run's
closure-race mock recursively intercepted the new publication transaction; its scoped
replacement passes. Intermediate logs remain available.

Import boundaries, compilation, activated environment, whitespace and application
link/fence checks pass for 118 application documents. The repository-wide documentation checker still includes
incoming saved research websites with missing website assets; application documents
are checked separately without modifying that checker or the research files.

All task-created services have drained. The failed recursive fixture was reconciled
only after confirming its pytest process had exited; two completed module fixtures
were stopped through the normal service API. The Tool fixture now explicitly owns
its service teardown. No forced kill, live Docker/Linux/BPF experiment, deployment,
container build or host-network modification occurred.

### Review boundary and preservation

The incoming snapshot in this evidence root precedes this pass. `repair-only.diff`
and `repair-files.json` distinguish the structural repairs from accumulated Feedback,
earlier fixes and unrelated work. `preservation.json` checks captured non-task files,
research/navigation, and the separate SEED checkout. SEED's 1,817 captured files,
status, branch, HEAD and index are unchanged; both repository indexes and HEADs are
unchanged. Accepted outputs were not write targets. Nothing was staged, committed,
merged, renamed or published.

Remaining limits: evidence gaps stay unknown; Linux sender observations cannot supply
complete intermediate hops. Historical MVCC reads depend on retention, meaningful
record storage still grows with actual changes, and there is no crash recovery or
exactly-once external-effect guarantee. Preserve receipts for ambiguous attempted
adoption; cancellation does not authorize rollback. One Linux and one BPF live run
remain for the user's subsequent stage after independent review.


## Final targeted live integration validation — 2026-09-23

The user reported that the structural source/focused-evidence review passed and
explicitly authorized representative live Docker/Linux and Docker/BPF validation.
This section records implementation-side live evidence, **not independent acceptance,
whole-round acceptance, or user visual acceptance**.

Evidence root:
`/home/lionheart/workspace/skyseed-local/experiments/feedback-live-20260923/`.
Raw timestamps use UTC on September 22; the local validation date is September 23
(Asia/Shanghai). No application or SEED source repair was needed. The only repository
edit in this pass is this report. Temporary assembly, independent host/container
programs, verification scripts, and every output remain under the evidence root.

### Source, environment, and exact execution

Incoming SkySEED HEAD: `e7a7c9bb53655fe85b5eb6c4ba2398a04ef0a7c1`, branch
`feature/skyseed-v2`, with the incoming Feedback, earlier repairs, and research changes
preserved. `baseline/{skyseed,seed-emulator}/` contains HEAD, branch, status, index diff,
working diff, and per-file SHA-256 manifests; SkySEED source copies exclude research.
`baseline/environment.json` records Python 3.12.13 at
`/home/lionheart/miniconda3/envs/skyseed_dev/bin/python`, Conda prefix
`/home/lionheart/miniconda3/envs/skyseed_dev`, and actual SkySEED/SEED import paths.
Docker Engine is 29.6.2 on Linux `7.0.0-31-generic`, x86_64. Existing bpffs and libbpf
were used. No dependency installation, host-wide setting change, or Git write occurred.

The following commands were run from the evidence root after activating the existing
environment. Generation is retained, so each `scene.py` invocation ran in a separate
process while the independent host program drove the generated shell entries.

```bash
source /home/lionheart/miniconda3/etc/profile.d/conda.sh
conda activate skyseed_dev
python capture_baseline.py
python scene.py linux linux > linux-generation.log 2>&1
bash linux/deploy.sh > linux-deploy.log 2>&1
python host_validate.py linux > linux-host.log 2>&1
python scene.py linux linux-all-zones > linux-all-zones-generation.log 2>&1
python scene.py bpf bpf > bpf-generation.log 2>&1
python remaining_runs.py > remaining-runs.log 2>&1
```

`scene-initial.py` and `host_validate-initial.py` retain the harness versions used by
the first Linux run. `scene.py` then fixed the workload coverage, and `host_validate.py`
added the already-frozen-position request and live qdisc readback before the subsequent
runs. `remaining_runs.py` waits for the first run to drain, then invokes, sequentially:

```bash
bash linux-all-zones/deploy.sh > linux-all-zones-deploy.log 2>&1
python host_validate.py linux-all-zones > linux-all-zones-host.log 2>&1
bash bpf/deploy.sh > bpf-deploy.log 2>&1
python host_validate.py bpf > bpf-host.log 2>&1
```

For each output, `host_validate.py` starts `bash OUTPUT/run.sh`, requests shared
shutdown, waits for the runtime process and both owner publications to close, executes
`bash OUTPUT/cleanup.sh`, then calls `interaction_service.stop_interaction(OUTPUT)`.
It retains run/cleanup logs, request receipts and results, sampled public owner state,
container stdout, live kernel readbacks, and an export of the scoped etcd records before
stopping the service. The forensic record export does not substitute for the public
interaction checks. The normal `run.sh` entry generates the final observation views.

### Scene and coverage

Both representative outputs reuse Tiny's established builder: two satellites, one New
York GS, and one ordinary ground router/AS with its IX. The explicit route strategy is
`GatewayHopCountRoutePlanningModel(gateway_candidate_count=1)` in **both** backends;
Linux runs real kernel interfaces/routes/netem, rather than relying on its empty default
route Model or claiming OSPF qualification. BPF uses the real kernel tc dataplane.

The initial Timeline is `2026-01-01T00:00:00Z` through 180 seconds, observation cadence
10 seconds, requested delivery duration 30 seconds, preparation lead fraction 0.75,
capacity factor 2, deployment/runtime workers 2, and route workers 1. A public
continuation adds 60 seconds. Each run activates all 25 distinct boundaries from 0
through 240 seconds across eight delivery windows. Actual starts are 0, 30, 60, 90,
120, 150, 190 and 220 seconds; the finite initial last window includes 180 seconds.
A captured preparation hook records the actual B0 Runner snapshot before formal time.

The existing `tests.integration.test_feedback_revision.FeedbackParameters` fixture is
supplied through normal Simulator assembly. It reads the unrestricted user key `delay`;
values 900 and then 1800 change connection delay in future candidate material. This is
a small validation fixture, not a new research algorithm or permanent Example. The
parameter Model retains its existing authored memory behavior.

**Initial harness coverage failure, retained:** `linux/` used the default stable pair,
New York GS ↔ `americas`. That Zone has no service source in this scene. Controls,
revision, progression, and cleanup succeeded, but that workload could not establish a
positive reply path. Its timeouts and missing-source evidence remain intact. This was
corrected only in the temporary harness by selecting **all declared Tiny GS/Zone pairs
from static identities before startup**, in both directions, then repeating Linux in
fresh `linux-all-zones/`. BPF uses the same four communications. No route-aware
selection, mid-run pair replacement, or production-code change was introduced.

Traffic cadence is one second and intended wall duration 600 seconds, allowing one
persistent sender segment to span delivery, pause/resume and revision. The normal
240-second-end shutdown stops those still active. Internet Map is disabled; BPF packet
telemetry is enabled only for BPF. Container access uses the normal provisioned
`/opt/skyseed-interaction/skyseed_interaction.py` and etcd Unix socket, with a declared
read-only Node shared-folder mount of the existing `skyseed_dev` environment. It runs
inside the actual GS container and records its hostname, PID, and network namespace;
the independent host program records its different PID/namespace.

### Public interaction and selection evidence

All representative host/container controls below completed with `SUCCEEDED`. Host
`host-value` is read by the container, which writes `container-value` containing that
value and its hostname; the host reads it back. Both programs read available computation
and runtime state. The container submits `container-bindings` and observes its receipt
and result. Host requests include `host-bindings`, `host-pause`, `host-resume`,
`host-continue`, `host-revise`, `host-revise-late`, material queries and inspection.
Pause is held and checked for three seconds at the 10-second frontier, then released.
Both revision requests run after resume without another pause.

Full receipt identities include experiment, owner, owner session, request ID and accepted
etcd revision. Exact records are in `CASE-evidence/*-{receipt,result}.json` and
`container-stdout.json`; `evidence-summary.json` consolidates them.

| Output | Experiment ID | Runtime invocation |
|---|---|---|
| `linux` | `aa4231185aaa4ce8abeb8c23560e1e1b` | `20260922T191603.487634486Z-2236165` |
| `linux-all-zones` | `b3c72ac9d13849399a0becf0e35ae5e3` | `20260922T192040.144972795Z-2240241` |
| `bpf` | `0e95a53e92dd471c82e4924fd2b20010` | `20260922T192524.355448742Z-2244832` |

**linux-all-zones:** computation session `388678c2fa5f4e9f8f3b3d3172f4b7cc`; runtime session `0dd24adcb6e14ddbaa1c595ba6aab6b8`.

| Request ID | Accepted revision | Requested → actual position | Selected material version / first window |
|---|---:|---|---|
| `host-pause` | 260 | `None` → `2026-01-01T00:00:10+00:00` | — |
| `host-resume` | 277 | `None` → `2026-01-01T00:00:10+00:00` | — |
| `host-continue` | 282 | `None` → `2026-01-01T00:04:00+00:00` | — |
| `host-revise` | 297 | `2026-01-01T00:01:30+00:00` → `2026-01-01T00:01:30+00:00` | `f8470029e57f4c70b1b2bb18ceb369cd` / 3 |
| `host-revise-late` | 659 | `2026-01-01T00:02:00+00:00` → `2026-01-01T00:02:30+00:00` | `30cabf697b754148a8eec0e92e29750e` / 5 |

Runtime adoption request IDs: `adopt-12881cb23d9ab83fddfcb22d`, `adopt-b4a8a8da4f08b103bda78958`.

**bpf:** computation session `50caccc8cfd2480382267de0108deab2`; runtime session `6ede2d8a177841849b06c6df7839def8`.

| Request ID | Accepted revision | Requested → actual position | Selected material version / first window |
|---|---:|---|---|
| `host-pause` | 538 | `None` → `2026-01-01T00:00:10+00:00` | — |
| `host-resume` | 556 | `None` → `2026-01-01T00:00:10+00:00` | — |
| `host-continue` | 561 | `None` → `2026-01-01T00:04:00+00:00` | — |
| `host-revise` | 575 | `2026-01-01T00:01:30+00:00` → `2026-01-01T00:01:30+00:00` | `cb811d813ef144ee9a8a34cec8b3c056` / 3 |
| `host-revise-late` | 935 | `2026-01-01T00:02:00+00:00` → `2026-01-01T00:02:30+00:00` | `82921528798e45d7924c0983cc23e073` / 5 |

Runtime adoption request IDs: `adopt-39b7cde0b4868be4081ad342`, `adopt-59d0cf978434b077d1c87e25`.

For both representative cases, the first request targets 90 seconds / window 3 while
`frozen_through=1`; it adopts five future windows. The second targets 120 seconds /
window 4 while `frozen_through=4` at the 110-second active frontier; it selects 150
seconds / window 5 and recalculates three windows. Its `attempts` list is empty:
checkpoint eligibility has already moved past the frozen window. This is demonstrated
handling of an already-late requested position, **not a live candidate losing an
in-flight preparation race**. That latter race retains earlier focused evidence only.

`materials-before-result.json`, `materials-before-late-result.json`, the selection
snapshots and runtime adoption records establish the request/freeze/selection facts.
`runner-events.jsonl` records the actual candidate path and SHA-256 at `batch_load_start`,
then readiness and activation. `summarize.py` checks those hashes against the candidate
files and checks all 25 distinct activations through 240 seconds. Both selected revisions
are actually loaded and followed by further execution; only writing `selection.json`
is not treated as execution. Original materials and rejected/superseded candidates remain.

Linux live `ip -j` and `tc -j` captures show actual stable identities, routes, links,
and a netem delay change from approximately 102 µs to 1,012 µs after the first revision.
BPF live tc filter readbacks show JIT-compiled `skyseed_tc` programs on all three
SkySEED nodes, with program IDs 19689, 19691 and 19693 at the first readback. Its raw
kernel packet telemetry provides separate actual dataplane evidence. These observations
are in `CASE-evidence/{before,after}-revision-kernel.json` and the invocation Traffic log.

### Traffic observations and generated views

| Case / fixed direction | Observed replies | Observed timeout lines |
|---|---:|---:|
| linux-all-zones: americas / ground-to-zone | 0 | 238 |
| linux-all-zones: americas / zone-to-ground | 0 | 0 |
| linux-all-zones: new-york-metro / ground-to-zone | 133 | 108 |
| linux-all-zones: new-york-metro / zone-to-ground | 133 | 0 |
| bpf: americas / ground-to-zone | 0 | 238 |
| bpf: americas / zone-to-ground | 0 | 0 |
| bpf: new-york-metro / ground-to-zone | 133 | 108 |
| bpf: new-york-metro / zone-to-ground | 133 | 0 |

Both metro directions receive replies before and after the 90-second material revision
using the same sender segment. Around 130 seconds, the scene withdraws metro service:
the Zone sender drains, and the GS sender continues to record timeouts. The `americas`
Zone never supplies a reverse sender; zero replies there are not zero packet loss.
The missing source is explicit. No sender is restarted merely for window delivery or
material selection. `probe-execution.jsonl` retains packet IDs, sequence observations,
active frontiers, source bindings and actual stop reasons.

Normal Traffic termination uses its owned TERM path and confirms cleanup. In these
runs ping does not emit a terminal summary, so aggregate `sent`, `received`, and `loss`
remain **null**. Observed reply/timeout counts above are evidence counts, not reconstructed
transmit totals or an invented loss percentage. Gaps stay unknown.

- `linux-all-zones/visualizations/traffic/traffic-replay.json`: 4 communications, 624 events, 0 events with recorded hops; telemetry completeness `null`, losses `null`.
- `bpf/visualizations/traffic/traffic-replay.json`: 4 communications, 2036 events, 1412 events with recorded hops; telemetry completeness `true`, losses `0`.

Linux views contain zero intermediate hops. BPF views use recorded packet observations;
telemetry completeness/loss metadata does not prove universal path coverage or lossless
communication. Final Runner, Traffic, Performance and aggregate view generation succeeds
through normal `run.sh`. Firefox checks exercise communication selection, event detail,
seeking, globe reset and playback on the actual generated Traffic pages. They are
implementation browser checks, not user visual acceptance. The first browser attempt
received HTTP 502 through the inherited localhost proxy; setting only that process's
`NO_PROXY`/`no_proxy` to `127.0.0.1,localhost` resolves it. Raw failed and passing logs remain.

Verification commands (from the same activated environment/evidence root):

```bash
python -m py_compile scene.py host_validate.py container_client.py
python summarize.py
NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost python check_traffic_browser.py linux linux-all-zones
NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost python check_traffic_browser.py bpf
python verify_cleanup.py
python verify_preservation.py
```

### Shutdown, preservation and limits

For every run, `host-shutdown` receipts/results precede owner closure; runtime exits 0,
its final handoff records completed Traffic and Runner cleanup without primary error,
and generated deployment cleanup exits 0. Each deployment removes six owned containers
(including its generated support services) and three networks. Raw cleanup events
confirm Linux identity-interface/connection removal and BPF pin-root/resource removal.
Every launched Traffic segment has a corresponding `generator_stopped` with cleanup
confirmed. The recorded node PIDs and owned etcd PIDs are absent, and etcd sockets are
removed after both owners close. `cleanup-verification.json`, final Docker inventories,
and `CASE-evidence/owners-closed.json` retain these checks. Docker build caches are
retained; no shared image cache or unrelated resource was pruned.

`preservation.json` verifies incoming SkySEED files except this report, all 1,817
captured SEED files, unchanged repository HEAD/branch/index, and all 5,553 captured files
under accepted `examples/Sky06_s4_native_ospf/output`. `validation-only.diff` isolates
this report increment. No staging, commit, push, merge, naming migration, Etcd dashboard,
or host-wide configuration change was performed.

**PASS within this scope:** real Linux and kernel BPF generation/deployment/preparation;
eight delivery windows and 25 activations; independent host and actual container
interaction; pause/resume and retained continuation; active future revision and actual
selected-material activation; later eligible selection for an already-frozen requested
position; persistent fixed communications with positive metro replies across revision;
retained loss/missing-source evidence; generated observations; normal owned-resource drainage.

**Failure/limitation retained:** the initial default Linux workload lacked a positive
reply path, corrected by the static all-Zone harness workload in a fresh output;
`americas` lacks service and metro service later withdraws; aggregate ping totals remain
unknown. The initial proxy-affected browser check failed before page testing and passed
with the documented process-local environment correction.

**Unproven here:** an in-flight live preparation/adoption race, long-duration performance,
large-scene/OSPF convergence, crash recovery, arbitrary authored Models/dataplanes,
all-pairs reachability, lossless traffic, complete Linux intermediate transit, independent
whole-round acceptance and user visual acceptance. No additional full matrix was run.

**Architecture Delta:** none in application/public methods, records, ownership,
dependencies, execution paths, or permanent Example behavior. Only this report and
external temporary validation harness/evidence were added or changed.

## Temporary satellite-failure and replanning validation — 2026-09-23

This addition exercises an authored fault/feedback/adaptation scenario on the current
implementation. It does not reopen or replace the previously reviewed evidence, and
it does not claim independent acceptance or user visual acceptance.

### Scope, source and reproducible commands

All temporary code and new outputs are under
`/home/lionheart/workspace/skyseed-local/experiments/feedback-failure-20260922T200507Z`
(below, `E`). The application source used is incoming branch `feature/skyseed-v2`,
HEAD `e7a7c9bb53655fe85b5eb6c4ba2398a04ef0a7c1`, with the incoming dirty tree
captured in `E/baseline/skyseed/{head,branch,status,diff,staged,files.json,source/}`.
The separate SEED checkout has its own baseline. These runs do not represent clean-HEAD
qualification. Python is `/home/lionheart/miniconda3/envs/skyseed_dev/bin/python`,
3.12.13, Conda prefix `/home/lionheart/miniconda3/envs/skyseed_dev`; kernel
`7.0.0-31-generic`, Docker 29.6.2. No dependencies or host-wide settings changed.

Commands used from `E` after activating the existing environment:

```bash
source /home/lionheart/miniconda3/etc/profile.d/conda.sh
conda activate skyseed_dev
python capture_baseline.py > baseline.log 2>&1
python scene.py linux linux > linux-generation.log 2>&1
python scene.py bpf bpf > bpf-generation.log 2>&1
bash linux/deploy.sh > linux-deploy.log 2>&1
python run_case.py linux > linux-host.log 2>&1
python remaining.py > remaining.log 2>&1
python capture_bpf_filters.py > bpf-filter-readback.log 2>&1
```

The two `scene.py` commands run in retained computation sessions. `remaining.py`
waits for successful Linux cleanup, then runs `bash bpf/deploy.sh` and
`python run_case.py bpf`, retaining `bpf-deploy.log` and `bpf-host.log`.
`run_case.py` launches normal `bash CASE/run.sh`, uses public interaction receipts
and results, shuts down both owners, joins runtime/tool generation, runs normal
`bash CASE/cleanup.sh`, and then stops the owned Etcd service after closing its client.
A final read-only export of scoped Etcd records is retained before stopping it;
that forensic export is not the Model/control interface. Runtime logs and all raw
requests/results remain under `E/CASE-runtime.log` and `E/CASE-evidence/`.

### Scene and temporary Model

`scene.py` reuses `Sky00_tiny_network.build_emulator()` and publicly adds one satellite.
Stable identities are G=`65001/ground-station/gs-new-york`,
A=`65001/satellite/satellite-0`, B=`65001/satellite/satellite-1`,
C=`65001/satellite/satellite-2`. The static, explicitly **synthetic** candidate graph
contains ISLs A–B and B–C, ordered ground candidates G–A and G–C, and both existing
Zone-service candidates on B. Synthetic static ECEF positions are `(7000000, i*50000, 0)`
for A, B, C, G. This establishes mechanism, not orbital realism or algorithm quality.

Both runs use 2026-01-01 00:00:00–00:05:00 UTC simulation time, five-second observations,
30-second delivery windows, two runtime/deployment workers and one route worker.
`GatewayHopCountRoutePlanningModel(gateway_candidate_count=1)` is explicitly supplied
for Linux and BPF. Normal simple ISL/GS and first-candidate Zone Models plan the links.
The parameter Model alternates 100/200 µs delay every five seconds at 10 Mbps, forcing
ordinary live parameter updates during the outage. Generated Linux routes/netem and
the actual kernel BPF dataplane are used.

The workload is fixed before runtime: G ↔ `new-york-metro`, one ping per second in
both directions, 600-second configured ceiling, bounded by normal five-minute run
shutdown. Stable identities are `communication-3f1f6d5ed6810b13` (G→Zone) and
`communication-7ac019e7e1b8c466` (Zone→G). The service stays on B. No pairs, sources or
Zones are chosen after observing reachability; both senders persist in segment 1.

Temporary `FeedbackLinks` in `models.py` is passed through the supported Simulator
constructor. Each observation opens a public InteractionClient, reads
`user/failed-satellites`, closes that client in `finally`, and filters every ISL,
GS-access and Zone-service candidate involving a failed satellite. It uses
`dataclasses.replace` on the ordinary static result, preserving timestamp and unknown
work/search metadata. It advertises neither limited search nor contact prediction,
holds no long-lived client, and performs no private World/Planner edits. Per-call
feedback revisions and resulting candidates are recorded in
`CASE-model-observations.jsonl`. PhysicalWorld identities remain all four nodes,
including A, in every inspected compiled boundary. No sealed materials are edited.

### Fault and observation semantics

`fault.py` checks A's container/PID binding and administratively downs only its
experiment-created `sv2*` veths. BPF additionally has its two local-output veths;
these are included. The injector preserves the container, stable identity interface
and management execution, recording a successful `docker exec` management check.
It does not assume Linux firewall rules intercept BPF forwarding. Actual link flags,
incarnations and loss of both fixed communications establish the effect separately
on each backend. Every two seconds while armed it checks for newly created, replaced
or unexpectedly UP business veths and fails on any such escape. It does not reapply
the injection and mask Runner behavior. Ordinary parameter updates continue.

`fault-ground-truth.jsonl` is separate from `observer-decision.json`. The authored
observer reads sender observations after a three-second settling allowance; its rule
requires at least ten timeout lines and zero replies in **each** fixed direction.
It combines those symptoms with predeclared baseline-transit hypothesis A, then
publishes A's stable identity through `user_put`. It does not localize arbitrary
failures, read the injector's ground-truth log, or claim a general failure detector.
The orchestrator supplies the observation interval. Both runs observe 24 timeout
lines per direction and zero replies at that decision point.

Normal replanning removes A's affected business links and selects G–C–B. The fault
stays armed until alternate-path replies, live kernel snapshots and recovery captures
have been recorded. Deleting the now-excluded old veths is ordinary plan realization;
no container is killed or removed to simulate failure. After recovery, the injector
restores surviving original interface incarnations, records already-absent ones,
clears the user set and requests restoration. Linux's old business veths are already
absent; BPF's surviving local-output pair is brought back UP. The restoration plan
reintroduces A–B with new live veths. Policy retains G–C–B, which is valid restored
eligibility without requiring a move back to A.

### Requests, selected material and actual activation

Both cases complete **10 delivery windows and 61 distinct activations** through 300
seconds. No pause is introduced for candidate calculation. At the failure request,
active time is 45 s and the preparation freeze is window 2 (60–85 s); selection starts
at window 3 / 90 s. At restoration, active time is 110 s and the freeze is window 4
(120–145 s); selection starts at window 5 / 150 s. Both requests use feasible future
windows, succeed on `attempt-000`, and require no deferral. No race is manufactured
or boundary weakened. The earlier late-position evidence remains separate.

Request IDs are `failure-revise` and `restore-revise`, with accompanying
`*-materials-before` / `*-materials-after` inspection requests. Receipt, complete
status history, result, pinned completed publication and selection are retained in
`CASE-evidence/`. These identify candidate adoption separately from actual Runner use.

| Case | Experiment ID | Computation session | Runtime session | Failed-set / cleared-set Etcd revision |
|---|---|---|---|---|
| linux | `7e8544988db24d3d9ef44baff5aedbb9` | `0f79f09edf1a4fb2b3225c9e6e2c04b3` | `413dbcd28ade47a4a8a3c266bb83842e` | 340 / 575 |
| bpf | `f8db791f4d1845dea01b4f773835f6a7` | `90883ce2b0804d01a9b2d926c364bf93` | `1fc323e81ac34e4db001a4475d18b799` | 601 / 815 |

| Case / request | Receipt revision | Requested / selected / activated (s) | Frozen index | Selected version | First manifest SHA-256 |
|---|---:|---|---:|---|---|
| linux / failure-revise | 345 | 90 / 90 / 90 | 2 | `625cfd6fdac14ef097e51c736a96a8a3` | `93f3d70b227cc5e056a1d104862624095f8eec3845a63b4311993175a27b427b` |
| linux / restore-revise | 582 | 150 / 150 / 150 | 4 | `e532c2f5280c4619a28e365cab43813c` | `403e5cf6a2a4bd55ae534c6d43b8d10d946f7c9b5882a259567f59ba582acc1c` |
| bpf / failure-revise | 606 | 90 / 90 / 90 | 2 | `c7fcc5da39ca4cd597d332355b442f99` | `9a843d9770a85290752bb95cd7d032a9d3f5afa1bfa6011c9a43a6631865eb0a` |
| bpf / restore-revise | 821 | 150 / 150 / 150 | 4 | `e09c3c7777874e45a256de820863e626` | `dedfa222ee921948bf019b544525e0de08ed7ab2633b2741d6fe84fcb914e0a7` |

Manifest paths are `results/revisions/VERSION/attempt-000/window-003/manifest.json`
for failure and `window-005/manifest.json` for restoration. `verify_scenario.py`
compares the SHA-256 with `batch_load_start`, then checks readiness-order activation
and subsequent progression. It reads planner history through each saved completed
publication, replays selected compiled connection deltas and compares them with the
adopted operational state. `verified-plans.json` and `verified-compiled-materials.json`
show baseline G–A–B, adapted G–C–B with no A business connection, and restored A–B
eligibility. Model feedback adoption, planning, compilation and actual activation are
distinct evidence steps. Read-only material decoding here is forensic verification,
not a private control or mutation path.

| Case | Injection wall UTC (simulation s) | Observer decision wall UTC (simulation s) | Failure activation wall UTC (simulation s) | Fault removal wall UTC (simulation s) |
|---|---|---|---|---|
| linux | 2026-09-22T20:11:27.092479+00:00 (15) | 2026-09-22T20:11:55.012003+00:00 (45) | 2026-09-22T20:12:38.572+00:00 (90) | 2026-09-22T20:13:01.692404+00:00 (110) |
| bpf | 2026-09-22T20:17:08.775091+00:00 (15) | 2026-09-22T20:17:36.782033+00:00 (45) | 2026-09-22T20:18:20.323+00:00 (90) | 2026-09-22T20:18:43.319834+00:00 (110) |

linux: 46 armed fault checks and 14 successful ordinary activations between injection and revision activation; 43 Model observations read the failed set. bpf: 46 armed fault checks and 14 successful ordinary activations between injection and revision activation; 43 Model observations read the failed set. 
The raw Runner events retain completed Linux actions or BPF netlink/qdisc operation
counts during this interval. No unexpected interface UP/replacement is observed.
The clocks and host logs retain wall/monotonic time alongside simulation frontiers.

### Traffic and kernel evidence

Counts below are actual sender observation rows, **not inferred packet totals**.
The outage interval excludes the first three seconds after injection and ends at
actual failure-material activation. Recovery counts end at explicit fault removal;
they cannot be explained by restoring A. One transient timeout per direction around
adoption is retained where observed; no gap is filled with invented successes.

| Case / direction | Baseline replies | Outage replies / timeout lines | Replies while fault still armed | Replies after fault removal | Total observed replies / timeout lines |
|---|---:|---|---:|---:|---|
| linux / ground-to-zone | 19 | 0 / 67 | 22 | 186 | 227 / 70 |
| linux / zone-to-ground | 18 | 0 / 66 | 23 | 186 | 227 / 70 |
| bpf / ground-to-zone | 18 | 0 / 67 | 22 | 186 | 226 / 71 |
| bpf / zone-to-ground | 18 | 0 / 66 | 23 | 186 | 227 / 70 |

`scenario-verification.json` includes first recovered replies with wall time,
monotonic time, published frontier, ICMP identifier and sequence, plus raw segment
launch/stop records. `probe-execution.jsonl` additionally retains network-error and
stderr observations. Both senders use the same segment, source, destination and
communication ID throughout delivery, revision and restoration. Owned Traffic TERM
shutdown produces no ping terminal summary here, so `sent`, `received` and `loss`
remain null. No aggregate loss percentage or zero-loss claim is made.

Linux evidence includes `baseline-A-tcpdump.txt`, `recovered-C-tcpdump.txt`, and the
four `*-kernel.json` snapshots. They show real echo requests/replies traversing A
before injection and C while A remains excluded, the live G→Zone route changing from
`sv20692390b43a` to `sv2b1a99607b6a`, and A's lack of business links/routes after
adaptation. These captures do not become fabricated complete Linux hop records.

BPF `baseline-kernel-bpf-filters.json` records actual `skyseed_tc` programs on all
four nodes. `traffic-telemetry.jsonl` records actual kernel packet decisions, node
slots and ingress/egress interfaces. `network-verification.json` retains raw packet
examples for G–A–B and B–A–G before injection, then G–C–B and B–C–G during recovery
while still armed; it checks no A packet observations in that recovery interval.
It also verifies the new live G–C link against compiled interfaces and restoration
of A's live link. This is actual kernel evidence, not solely planner path inference.

| Case | Generated Traffic events | Events with recorded hops | Telemetry completeness / collector losses |
|---|---:|---:|---|
| linux | 731 | 0 | None / None |
| bpf | 3459 | 2859 | True / 0 |

Normal Runner, Traffic, Performance and aggregate observation generation succeeds.
Linux preserves absent intermediate telemetry. BPF collector metadata does not imply
lossless business communication or universal packet coverage. Firefox 154.0.1
functional checks on both generated Traffic pages exercise selection, event detail,
seek/reset and playback. These are implementation checks, not user visual acceptance.

Verification commands (the final checks are also orchestrated by `post_checks.py`):

```bash
python -m py_compile scene.py models.py fault.py observer.py run_case.py verify_scenario.py verify_network.py verify_cleanup.py
python verify_scenario.py
python verify_network.py
NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost python check_traffic_browser.py linux bpf
python verify_cleanup.py
python write_report.py
python verify_preservation.py
```

### Cleanup, preservation and result boundaries

Both `outcome.json` files pass. `shutdown` receipts/results close computation and
runtime through normal ownership paths. Runtime exits 0 after Traffic, Runner and
observation generation complete. Each fixed sender has one matching drained segment
with `cleanup_result=cleaned`. The injector is explicitly disarmed, its finite
six-second tcpdump measurements have exited via SIGINT, and it has no background
process. No slow work is force-killed. Generated deployment cleanup removes seven
owned containers and three networks per case with configured graceful hooks.
Kernel cleanup confirms Linux identities/connections and BPF pin-root removal;
owned helpers, node PIDs, computation and runtime processes are gone. Both Etcd
services stop after owner/client closure, with PID and socket absent. Exact cleanup
records, final Docker inventories and checks are in `cleanup-verification.json`.
Shared image/build caches remain.

`preservation.json` verifies incoming application files except this report, all
captured SEED files, all 5,553 accepted Sky06 output files, and all 2,003 prior live
evidence files. HEAD, branch and staged diff remain unchanged. `validation-only.diff`
isolates this report increment from incoming work. Temporary code is only under E;
no production repair, dependency change or new permanent Example was needed. No
staging, commit, push, merge, dashboard, rename, host-wide change or paper-writing
edit was performed.

**PASS in this experiment:** baseline dependence on A; actual persistent scoped
failure on Linux and kernel BPF; observed bidirectional outage before feedback;
public failed-set publication and Model adoption; safe future selection and actual
material activation while execution progresses; plan/material/live path excluding
A; fixed-flow recovery through C before fault removal; restored eligibility and
continued execution; generated views and normal complete owned-resource drainage.

**Observed failure, intentionally retained:** both fixed flows lose connectivity
after injection, including timeout/network-error rows and transient adoption-edge
observations. No unexpected production defect was exposed. Unknown ping totals stay
unknown. This synthetic test establishes mechanism only.

**Unproven here:** general fault detection/localization, orbital realism, route-policy
quality, arbitrary faults or Models, service-satellite failure/migration (B remains
service owner; the A Zone-candidate filter is not exercised by this live graph),
new-interface fault interception beyond the checked scenario, an in-flight preparation
race, all-pairs reachability, performance at scale, universal packet-path completeness,
independent whole-round acceptance and user visual acceptance. Earlier accepted
interaction/pause/continuation evidence is preserved, not rerun as a new full matrix.

**Architecture Delta:** none in public methods/types, records, ownership,
dependencies, production execution paths or permanent Example behavior. The additions
are this report and temporary experiment/evidence outside the repository.

## Final review repairs: result retirement and owner outcomes (2026-09-23)

This increment implements the two additional lifecycle findings. It does not rerun
the accepted Linux/BPF campaigns or claim independent acceptance. The incoming
implementation was uncommitted on `feature/skyseed-v2`, HEAD
`e7a7c9bb53655fe85b5eb6c4ba2398a04ef0a7c1`. The repair baseline, logs, retained test
outputs and isolated diff are under:

```text
/home/lionheart/workspace/skyseed-local/implementation-baselines/feedback-final-repair-20260922T210638Z
```

Below, `E` denotes that directory. `baseline/skyseed/source` contains the incoming
application source; `baseline/*/files.json`, HEAD/branch/status/staged records and
the accepted-output/evidence manifests capture incoming state. `repair-only.diff`
and `repair-files.json` isolate this increment from that baseline, rather than from
Git HEAD. All checks used `/home/lionheart/miniconda3/envs/skyseed_dev/bin/python`,
Python 3.12.13, `CONDA_DEFAULT_ENV=skyseed_dev`, with the existing separate
`/home/lionheart/workspace/seed-emulator` import. See `environment-check.log` and
`baseline/environment.json`.

### Completed effects survive result publication and session retirement

The control consumer now uses one settlement path for ordinary progression,
retirement and closure. It preserves a confirmed synchronous dispatch result,
checks asynchronous completion against owner facts, and expires only unfinished
work whose target has ended. An admitted dispatch with no known result remains
indeterminate; it is neither replayed nor converted into an adoption rejection.
Queued old-session requests still expire. Revision completion still comes from
the existing Producer revision status and material selection authority.

The owner publisher retains unresolved terminal publication decisions before any
store I/O. A retry recognizes a persisted terminal result, or writes the retained
decision once with its original history event. A later reset/closure cannot
overwrite a terminal result. This bounded, owner-local pending-publication state
does not scan request history, introduce another durable adoption authority, or
claim effect recovery after process loss.

Four causal adoption regressions combine real Etcd, the actual control consumer,
publisher, retained Producer/Compiler, material catalog and offline Runner. They
cover reset and closure, losing acknowledgements at RUNNING or terminal SUCCEEDED
publication. They assert one adoption effect, the preserved adoption/revision
result, expiration of an unadmitted future request, and actual candidate selection.
They then continue calculation by 20 seconds and check selected predecessor hashes,
prior-state fingerprints, the Compiler writer checkpoint and Runner material
identity. Thus the checks would detect selection/continuation divergence, rather
than merely accepting a different status label. Additional real-Etcd tests cover
all four terminal statuses with failures before and after commit, unknown dispatched
effects on reset/closure, and failed dispatch publication without re-execution.

### Cancellation reports actual owner drainage

Runtime shared state now carries the existing invocation outcome with separate
primary, Traffic and cleanup facts, plus a structured escaping owner error.
Secondary cleanup notes are captured before the outcome/handoff is written. A
handoff-release error remains visible even if local resource cleanup completed.
An early failure without an invocation outcome reports unknown Traffic/cleanup
status, not successful drainage. Invocation replacement clears stale resource
references and outcomes.

Calculation publishes an outcome after finite generation or retained computation
and accepted presentation have drained. Pre-Producer Model shutdown now settles
inside the finite control-owner context, before its closing publication; primary
startup errors retain secondary Model-drain errors. Retained presentation still
belongs to its existing owner and must finish before closure.

Cancellation and coordinated-shutdown results now wait for control-owner closure.
Known execution or drainage failure produces FAILED; SUCCEEDED requires a known
completed outcome. A closed owner without such facts remains indeterminate. Reset
cleanup failure similarly reports FAILED rather than claiming a replacement.
`live=false` alone certifies neither successful resource cleanup nor deployment
cleanup. Local cleanup is still independent of Etcd availability.

The new owner-path tests use the actual runtime dispatch, invocation, control and
cleanup sequence, with offline Runner/Traffic resource doubles for deterministic
failure injection. They hold drainage barriers to prove successful cancellation
does not complete early. Calculation tests use actual retained/finite controllers,
Models and Producer, including primary plus drain failure, failure before Producer
creation, and presentation still running after calculation has already FAILED.
Runtime tests cover Runner failure, Traffic failure, both failures, failed reset
and coordinated shutdown. They compare shared outcomes with retained Runner
handoffs and verify Traffic-before-Runner cleanup ordering.

Service-stop ownership/policy is unchanged: output/runtime claims and owner
publications exclude active owners; an explicitly closed failed owner permits an
explicit stop of its owned Etcd. The failed result persists across Etcd restart.
Stopping this store does not certify or retry Runner/Traffic cleanup and never
removes separately owned deployment resources. A store-outage regression verifies
local cleanup completes while remote state remains live and the request RUNNING.
Its test-only teardown publishes a fresh closed fixture session after joining the
actual offline owner so the fixture can stop its Etcd; this is not a production
recovery mechanism.

### Exact checks and results

Commands ran from `skyseed_v2/` after:

```bash
source /home/lionheart/miniconda3/etc/profile.d/conda.sh
conda activate skyseed_dev
E=/home/lionheart/workspace/skyseed-local/implementation-baselines/feedback-final-repair-20260922T210638Z
```

The following incoming-source runs intentionally fail and establish causality.
The explicit pytest `pythonpath` override selects the captured source instead of
the working tree:

```bash
python -m pytest -q -o pythonpath="$E/baseline/skyseed/source/skyseed_v2/src" \
  'tests/integration/test_feedback_revision.py::test_confirmed_adoption_survives_lost_ack_session_end_and_continues[reset-RUNNING]' \
  'tests/integration/test_control_owner_outcomes.py::test_runtime_results_wait_for_real_owner_drain[runner-cancel-runtime]' \
  'tests/integration/test_control_owner_outcomes.py::test_calculation_cancellation_preserves_failure_and_waits_for_owned_work[cancel-computation-both]' \
  --basetemp="$E/baseline-causal-tmp" > "$E/baseline-causal.log" 2>&1
python -m pytest -q -o pythonpath="$E/baseline/skyseed/source/skyseed_v2/src" \
  tests/integration/test_control_owner_outcomes.py::test_failed_calculation_cancel_waits_for_presentation_then_reports_failure \
  --basetemp="$E/baseline-premature-tmp" > "$E/baseline-premature.log" 2>&1
```

`baseline-causal.log`: **3 expected failures, 4.92 s**. Adoption selection remained
changed while the revision reported FAILED; Runner cleanup failure returned
SUCCEEDED; calculation/drain failure became EXPIRED instead of FAILED.
`baseline-premature.log`: **1 expected failure, 2.66 s**, showing calculation
cancellation already SUCCEEDED while accepted presentation was still held after
calculation FAILED. The earlier `baseline-regressions.log` did not override pytest's
configured source path and is not causal baseline evidence.

Working-source checks:

```bash
python -m pytest -q tests/unit/test_experiment_control.py \
  tests/integration/test_control_owner_outcomes.py tests/integration/test_feedback_revision.py \
  tests/integration/test_runtime_reset.py tests/integration/test_shutdown_handoff.py \
  --basetemp="$E/final-core-tmp" > "$E/final-core.log" 2>&1
python -m pytest -q tests/integration/test_experiment_workflow.py \
  tests/integration/test_initial_readiness.py tests/integration/test_retained_workflow.py \
  tests/integration/test_completed_ranges.py tests/integration/test_closing_review.py \
  tests/unit/test_retained_computation.py tests/unit/test_realization_producer.py \
  tests/unit/test_runner_batches.py --basetemp="$E/lifecycle-tmp" > "$E/lifecycle.log" 2>&1
python -m pytest -q tests/integration/test_initial_readiness.py \
  --basetemp="$E/readiness-final-tmp" > "$E/readiness-final.log" 2>&1
python -m pytest -q tests/integration/test_control_owner_outcomes.py tests/integration/test_experiment_workflow.py \
  --basetemp="$E/owner-final-tmp" > "$E/owner-final.log" 2>&1
```

| Evidence | Result and scope |
|---|---|
| `final-core.log` | **99 passed, 107.72 s**: control, adoption/revision, reset, shutdown and the first 18 owner-outcome cases |
| `lifecycle.log` | **115 passed, 1 failed, 10 warnings, 72.88 s**; the single failure was the obsolete future-delivery readiness fixture described below |
| `readiness-final.log` | **22 passed, 22.29 s** after correcting that fixture |
| `owner-final.log` | **73 passed, 34.99 s** on final source: all 20 owner-outcome cases and 53 workflow cases, including both startup modes |
| `outage-final.log`, `presentation-final.log` | Individual new cases passed in **2.31 s** and **2.75 s**; both are also included in the final 73-test run |

These counts overlap and are not presented as one combined fresh suite run. The ten
lifecycle warnings concern Python 3.12 multithreaded fork deprecation in existing
completed-range tests. Initial fixture failures remain in `new-regressions.log`,
`core.log` and `outage.log`: Model injection was moved from a read-only instance
attribute to its class; the resource-free fake owner now supplies an explicit
completed outcome; the shutdown test waits for the shared publication rather than
only local state; outage-fixture cleanup uses a fresh publisher session to avoid
reusing an immutable state-history identity. No false-success assertion was removed.
The old readiness fixture forged an unsealed next material and hooked an obsolete
append path; it now generates two real delivery windows through the normal scene
builder and observes the actual validated append path. Its readiness ordering
assertions remain. The initial outage fixture's service was subsequently drained
after its owner had exited; `outage-fixture-drain.json` retains that explicit action.

```bash
python scripts/check_dev_environment.py > "$E/environment-check.log" 2>&1
python scripts/check_import_dependencies.py > "$E/import-check.log" 2>&1
python -m compileall -q src/skyseed_v2/experiment tests/integration/test_control_owner_outcomes.py
python scripts/check_documentation.py --scope focused > "$E/documentation-check.log" 2>&1
python "$E/check_application_docs.py" > "$E/application-documentation-final.log" 2>&1
git diff --check
python "$E/verify_cleanup.py" > "$E/cleanup-verification.log" 2>&1
python "$E/verify_repair.py" > "$E/preservation-final.log" 2>&1
```

Environment, compilation and import-boundary checks pass (170 production modules,
507 internal edges). The general documentation check **fails** on local links in
incoming research HTML archives; those files and the checker were left unchanged.
The separate application-only check uses the unchanged checker's fence/link/anchor
validation, excludes research, and includes this historical report. This scope and
its **103-document PASS** are recorded in `application-documentation-final.log`,
rather than calling the general check a pass. Compilation and `git diff --check`
also pass; their final logs are `compile-final.log` and `diff-check-final.log`.

### Preservation, ownership delta and remaining limits

`preservation.json` verifies that only the 12 listed repair files changed: five
production files (the service-stop file has documentation only), five test files,
the control contract reference and this report. All other incoming application
files, all 1,817 captured SEED files, all 5,553 accepted Sky06 output files, all
2,003 ordinary Feedback evidence files and all 1,927 satellite-failure evidence
files retain their hashes. HEAD, branch and staged diffs are unchanged in both
checkouts. `cleanup-verification.json` checks all **305 owned test-service configs**
under E for normal stop, disappearance of their recorded PID/start identity and absence
of its client socket, plus absence of remaining E-scoped processes. Docker
container/network/image inventories match the baseline. Test artifacts and logs
remain under E. No Docker/BPF experiment, host setting or dependency was changed;
no staging, commit, push, merge, permanent Example or research edit was performed.

**Architecture Delta:** public callable signatures/types, component ownership,
dependencies and permanent Example behavior are unchanged. Shared owner records
add bounded `outcome` facts and structured runtime `error`; cancellation terminal
results now accurately require known owner completion and preserve failure details.
Result publication/retirement and early Model shutdown ordering change internally.
Producer/Compiler revision semantics, preparation freezing, material selection
authority, nonblocking Runner progression and separate deployment ownership remain
unchanged. The precise contract is maintained in
[experiment control](../development/experiment-control.md).

**PASS:** the two reported lifecycle defects are repaired in the exercised causal
and owner-path combinations, including continued selected-material lineage,
terminal acknowledgement recovery, truthful cancellation/cleanup outcomes and local
cleanup during a store outage. **Remaining limitations:** these are real-Etcd and
offline tests with scoped resource failure injection, not new live kernel/Docker
qualification. Unknown dispatched effects, process-loss recovery and unreachable
store publications remain indeterminate; this repair introduces no new recovery
authority. The unrelated research-archive documentation failure remains. Results
were implementation evidence awaiting independent review at the time of this pass,
not a claim of independent or user visual acceptance. The later review status is
recorded at the top of this report.
