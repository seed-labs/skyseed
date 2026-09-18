# Phase 4b-2 closing campaign — 18 September 2026

This record covers the authorized sequential Docker/BPF campaign after the three
review fixes, removal of Coordination injection, and architecture documentation
changes. The local date is Asia/Shanghai; command records use UTC, beginning on
17 September. This is implementation and live experiment evidence. Independent
review, user visual acceptance, naming and release preparation remain separate.

## Source and reference conditions

Skyseed used branch `feature/skyseed-v2`, HEAD
`375501cfba5ee385486105f382324729914dc3b1`, with the incoming tracked and untracked
work preserved. The closing changes are measured against the captured incoming
workspace, not against that cumulative HEAD. SEED remained clean on
`extnode-upstream-development`, HEAD
`05ad40bd22374496691d4b4fd516b4ee9b36eb4b`.

The existing `skyseed_dev` environment supplied Python 3.12.13 at
`/home/lionheart/miniconda3/envs/skyseed_dev/bin/python`, Astropy 8.0.1,
PyYAML 6.0.3 and pytest 9.1.1. The host was Ubuntu 24.04.4, Linux
7.0.0-31-generic, an i9-14900K with 32 logical CPUs and about 60.5 GiB RAM.
These are measured reference conditions, not minimum hardware requirements.
No dependencies, environments, SEED changes or host configuration changes were
introduced. Existing Docker permissions and the default context were used.

Every case used its normal Python entry and normal `output/`, retained `serve`,
Compiler cutoff, active Traffic and optional Tools. Scene scale, physical parameters
and Models were unchanged. No worker override was requested: deployment used eight
workers; runtime and routing retained their supported defaults. Per-case evidence
records the effective worker use. The nominal timeline starts at
`2026-01-01T00:00:00+00:00`; Tiny has a ten-second cadence and all other cases five
seconds. Bounds and ACTIVE counts below include B0.

Default parameter settings include 100 ppm packet loss on ISLs and 200 ppm on GS
access links. A missing reachable-path reply can be compatible with those rates,
but the campaign does not attribute an individual loss without evidence. Traffic's
reported unexplained loss remains visible. Planned unreachable routes and unserved
Zones are separate calculated outcomes, not infrastructure failures.

## Commands and retained evidence

From the project root, each entry was launched through the existing environment:

```bash
/home/lionheart/miniconda3/bin/conda run --no-capture-output -n skyseed_dev python examples/Sky00_tiny_network/tiny_network.py
# Then the corresponding Sky01, Sky02, Sky03, Sky04 and Sky05 entries, sequentially.
# Sky04 alone starts with --duration-seconds 300.
```

For each published initial range the driver invoked `output/deploy.sh`, then
`output/run.sh`, and read `output/experiment_control.py status`. After the requested
last ACTIVE boundary it issued `cancel-runtime`, waited for Runner/Traffic and
runtime Tools, issued `cancel-computation`, waited for the retained entry, and ran
`output/cleanup.sh`. Cancellation after verified coverage is an intentional end to
retained waiting. The driver has no wall-clock abandonment or forced kill path.
S4 additionally uses `pause`, `resume` and `reset`; Sky04 uses two
`continue --seconds 300` requests with the observed timing described below.

Original generated materials, calculated records, invocation records and browser
views remain under each normal Example `output/`. Compact evidence lives beside
the repository at
`/home/lionheart/workspace/skyseed-local/experiments/phase4b-2-closing-20260917T184610Z/`:

- `incoming.tar.gz`, `incoming-files.json`, incoming status/HEAD, and the final
  `phase-only.diff.gz`, `source-and-docs.diff`, `phase-only-manifest.json`.
- `campaign/<case>/commands.jsonl`, `source-sha256.json`, generation/deployment/runtime/
  cleanup logs, changing control statuses and sampled host resources.
- Per-case `summary.json`, `analysis.json` and `verification.json`, plus the
  campaign driver and analysis scripts. Status `EXECUTED` means the requested
  runtime coverage and drainage completed; it does not replace Traffic's result.
- `checks/`, `architecture/` and `mermaid/` contain focused checks and actual rendered
  diagram evidence. The incoming Tiny output was preserved in `incoming-outputs/`.

Docker ownership and available host resources were checked before each case.
Cleanup is restricted to the case's Runner resources and Compose project; existing
image caches and unrelated host resources are retained.

## Live results

| Example | Scene: satellites / GSs / Zones | ACTIVE coverage | Traffic | Cleanup |
|---|---|---|---|---|
| Tiny | 2 / 1 / 2 | 0–30; 4 boundaries | PASS; 148/148 replies | Complete |
| S5 local Models | 172 / 10 / 184 | 0–600; 121 boundaries | WARN; 1,471/1,472 | Complete |
| S4 default | 348 / 355 / 732 | 0–600; 121; reset 0–30, 7 | WARN main; PASS reset | Complete, both invocations |
| S3 OSPF | 720 / 355 / 732 | 0–600; 121 boundaries | WARN; 1,086/1,459 | Complete |
| Continuation | 348 / 355 / 732 | 0–900; 181 boundaries | WARN; 2,142/2,154 | Complete |
| Five Shells | 4,408 / 355 / 732 | Replay 0–600; 121 boundaries | WARN; 1,471/1,489 | Complete, both attempts |

Reply counts alone do not express conformance: expected unreachable paths,
unexplained losses and unclassified windows are separated below. All six final main executions have intended
coverage PASS, zero infrastructure failures and zero telemetry ring loss. The failed
first five-shell attempt remains separately recorded below.

## Tiny

Default two satellites, one GS and two Zones, plus its ordinary SEED integration
nodes. Four ACTIVE boundaries cover 0–30 seconds. Traffic received all 148 requests:
PASS, no infrastructure failures or telemetry ring loss. Runner and Traffic drained;
Deployer removed the owned containers and networks. Normal entry:
`examples/Sky00_tiny_network/output/index.html`.

## S5

The unchanged local Geometry/ISL/GS Models ran the full 172 satellites, ten selected
GSs and 184 Zones through all 121 ACTIVE boundaries from 0–600 seconds. The two
actual deliveries were 0–295 (60 boundaries) and 300–600 (61). The successor was READY
before the seam; no material wait occurred. Computation remained idle at completed
600 while runtime continued.

Traffic returned WARN: 1,471 replies from 1,472 requests, coverage PASS, zero
infrastructure failures, zero telemetry ring loss and no persistent failure.
The 39 episodes represented 26 probe identities in both directions, with six
simultaneous generators at peak. The one `unexplained-reachable-loss` was request
sequence 6 of `traffic-episode-1c16b740ae4c`, around nominal 457.579 seconds.
Reply forwarding was observed, but delivery to the source was unconfirmed. It was
not tied to an in-flight relevant transition; `campaign/s5/loss-review.json`
retains the timing and packet association. Configured random link loss is a possible
cause, not proven attribution. Sparse service and disconnected planned outcomes
remain visible in the Planner view.

All 121 formal frames match the physical, Planner, Compiler and Performance views;
Runner/Traffic views identify the completed main invocation. Runner and Traffic
drained and Deployer left no owned containers or networks. Normal entry:
`examples/Sky01_s5_custom_models/output/index.html`.

## S4

The default S4 scene contains 348 satellites, 355 GSs and 732 Zones. Its main
invocation covered all 121 ACTIVE boundaries from 0–600 seconds, with deliveries
0–295 and 300–600 and no material hold at the seam. Pause at 30 seconds retained
the same frontier and seven completed boundaries across two observations; resume
shifted the runtime clock by 4.190 seconds without advancing during the hold.

Traffic reported WARN: 1,434 replies from 1,448 requests. Conformance classified
nine losses as expected unreachable, four as unexplained reachable-path loss and
one as unclassified. Intended coverage passed; infrastructure failures, telemetry
ring loss and persistent failures were zero. The 36 episodes represented 29
identities across both directions. `campaign/s4/loss-review.json` preserves the
unexplained and unclassified request windows; their causes remain unresolved.

After main-run 600, reset drained the first invocation and initialized a new B0 in
`reset-e575b36d17694365b56a9334c28e7573`. It reached all seven boundaries through
30 seconds before explicit cancellation. Retained material hashes and Compose
container identities were unchanged. Both invocations reported completed Traffic
and Runner cleanup; Deployer subsequently removed owned containers and networks.

The normal `examples/Sky02_s4_default/output/index.html` views correspond to the full
main invocation, as generated by its `run.sh`. Separate reset views are retained at
`output/runs/reset-e575b36d17694365b56a9334c28e7573/visualizations/index.html`;
the partial replay is a control check, not another complete 600-second run. Its
68 requests all received replies (Traffic PASS), with no infrastructure or telemetry
loss errors; its Runner and Performance views identify the reset invocation.

## S3 OSPF

The full 720-satellite S3 scene, all 355 GSs and 732 Zones used the unchanged local
OSPF computational Route Model, default physical/selection Models and real BPF
execution. Calculation published 121 formal boundaries; the main invocation
`20260917T195649.324592488Z-969165` activated all of them through 600 seconds.
Deliveries covered 0–295 and 300–600 with no material wait at the seam.

Traffic reported WARN: 1,086 replies from 1,459 requests. Of the 373 missing replies,
363 matched expected planned unreachability, two were unexplained reachable-path
losses and eight were unclassified. The local Area/backbone algorithm can leave
paths unavailable; no fallback algorithm or disabled probe was substituted.
Intended coverage passed across 39 episodes and 33 identities in both directions.
Infrastructure failures, telemetry ring loss and persistent failure classifications
were zero. Request windows are retained in `campaign/s3/loss-review.json`.

The physical, Planner, Compiler, Runner, Traffic and Performance views match the
formal and runtime records. Runner/Traffic drained; Deployer left no owned containers
or networks. Normal entry: `examples/Sky03_s3_ospf/output/index.html`.

## Single-shell continuation

The default S4 scene (348 satellites, 355 GSs, 732 Zones) began with the explicitly
requested 300-second initial range. The early `continue --seconds 300` was issued
at ACTIVE B0, and completed while runtime was still early in the first range.
Runner crossed 300→305 with continuous supply.

At 600, Runner reported `material` waiting and retained frontier 600 and 121 completed
boundaries across two observations. Only then was the late `continue --seconds 300`
issued. The real material hold lasted about 62.311 seconds; the recorded clock shift
was 62.357 seconds. Runner resumed at 605 and reached all 181 ACTIVE boundaries
through 900 without resetting or redeploying.

The three actual deliveries were 0–300 (61 boundaries), 305–600 (60), and 605–900
(60). Six Plans/Compiler realizations and one prehistory frame were recorded
across the three requests. `campaign/continuation/material-hashes.jsonl` preserves hashes before the early
extension, before the late extension and after completion. All previously sealed
Compiler files and Traffic probe plans remained unchanged. Calculation returned
to idle after each completed request and terminated only on explicit cancellation.

Traffic reported WARN: 2,142 replies from 2,154 requests. Nine losses were expected
unreachable, two unexplained reachable-path losses and one unclassified. Coverage
passed across 53 episodes and 38 identities in both directions; infrastructure,
telemetry ring loss and persistent failure counts were zero. The long receive gap
includes the deliberate clock/material hold and is not itself proof of a routing
failure. Exact request classifications are in `campaign/continuation/loss-review.json`.

All completed-range views match 181 formal frames and the invocation's 900-second
ACTIVE frontier. Runner/Traffic drained; Deployer left no owned containers or
networks. Normal entry: `examples/Sky04_single_shell_continuation/output/index.html`.

## Five Shells

The full scene contains 4,408 satellites, 355 shared GSs and 732 Zones. Each GS
has ten physical access slots, with a limit of two in each Shell scope. The initial
retained calculation completed all 121 formal boundaries through 600 seconds,
with two deliveries (0–295 and 300–600), four Plans and four realizations.

The first execution activated all 121 boundaries and crossed the delivery seam
without a material hold, but Traffic failed during its second generator launch.
Under the full host load, the first Docker launch/registration took long enough
for Runner to advance from B0 through ACTIVE 10. Traffic reused its cached B0
frontier for the second episode and selected satellite `s4-orbit-008-satellite-0017`
after the Zone address had moved to `s4-orbit-008-satellite-0016`. `ping` exited 2
with `Cannot assign requested address`. This is a local execution defect, not
planned unreachability or an external blocker. The failed invocation
`20260917T211445.430837050Z-1480579` and its original Traffic FAIL remain retained. Only ten requests were recorded;
all received replies, but intended coverage was NOT PROVEN after the executor
stopped. Its 40 infrastructure classifications remain visible. The first attempt took
about 4,661 seconds overall: first material readiness 923 seconds, deployment 725,
Runner preparation 384, Runner drainage 58 and Deployer cleanup 656. Lowest sampled
available RAM was 4.57 GiB and highest host swap use 6.54 GiB; these measurements
remain separate from the replay and are not minimum hardware requirements.

Traffic now refreshes Runner's published ACTIVE frontier before reconciling each
episode, including hold resume. It coalesces already reconciled frontiers and does
not retry failed probes or reclassify their errors. Two focused regressions advance
Runner during the first generator's registration and verify the next source on
initial launch and resume. The full execution was rerun after cleanup, using the
unchanged completed calculation and sealed materials; this avoids repeating valid
orbital, planning and Compiler work.

The replay source audit matches all 47 generator launches (27 Zone sources) to
the source valid at the latest actual ACTIVE boundary before launch. It records
no source mismatch in the replay; the original mismatch remains separately
identified in `campaign/five/frontier-source-audit.json`.

The corrected replay `20260917T220117.960210802Z-1980328` activated all 121 boundaries
through 600 seconds, crossing 295→300 without a material hold. Traffic reported
WARN: 1,471 replies from 1,489 requests. The 18 missing replies comprise five
expected handover disturbances, nine unexplained reachable-path losses and four
unclassified requests. Intended coverage is PASS; infrastructure failures,
telemetry ring loss and persistent failures are zero.
The 40 episodes represent 35 identities; 47 generator segments include seven source
migrations. All five Shells have observed stable and transition coverage in both
directions, as recorded in `campaign/five/shell-coverage.json`. Per-Shell counts
represent overlapping planned-path coverage and must not be summed as executor totals.
The unresolved request windows remain in `campaign/five/loss-review.json`.

All previously sealed Compiler files and Traffic plans retained identical hashes.
The original calculation used the normal retained entry and was explicitly drained;
the rerun repeats only the affected deployment/runtime path after complete cleanup.
`campaign/five-initial/` preserves the failed attempt, including its original deployment
measurements, summary, analysis and failure review. Its runtime views are under the
failed invocation's `visualizations/`; the normal Example output now shows the replay.
`campaign/five/` identifies the replay and its generation/material references.

Both attempts drained Runner/Traffic and removed their own containers and networks.
The final physical, Planner, Compiler and Performance views match all 121 formal
frames; runtime views identify the corrected replay. Normal entry:
`examples/Sky05_five_shells/output/index.html`.

`five-scope-audit.json` records the shared ten-slot GS limit and two-slot per-Shell
limits, selected episodes in both directions for all five scopes, and 172 selected
path intervals. Those paths stay inside their Shell and use GSs only as endpoints.
This checks the tested scope behavior without claiming universal route reachability.

## Applicability of earlier runs

The late Traffic correction changes only
`tools/traffic/active/probe_execution.py` in the live source inventory. The other
five cases, including S4's reset invocation, were audited against their actual
Runner activation timestamps and planned source intervals. All 225 generator
launches (129 Zone-source launches) selected the source valid at the latest ACTIVE
boundary before launch. None had the stale-source mismatch reproduced in the
failed five-shell attempt. Their coverage, packet observations and classifications
remain applicable; they are not described as fresh executions of the later file.
Per-case `frontier-source-audit.json` and source hashes retain this distinction.

## Reference measurements

Times below are seconds, rounded to one decimal. First material readiness is a
calculation measurement; deployment and Runner preparation are separate stages.
S4 rows use its full main invocation, with the reset included in total case time.
Five-shell replay reuses the initial calculation: its first-material column refers
to that calculation, and replay elapsed time excludes it. Separate first-attempt
measurements remain in `campaign/five-initial/`.

| Case | First material | Deploy command | Runner prepare | Runner drain | Deployer cleanup | Case elapsed |
|---|---:|---:|---:|---:|---:|---:|
| tiny | 0.8 | 7.6 | 1.3 | 0.5 | 12.2 | 58.6 |
| s5 | 16.4 | 21.0 | 3.9 | 1.7 | 20.3 | 698.8 |
| s4 | 62.2 | 70.9 | 14.3 | 3.7 | 44.4 | 970.5 |
| s3 | 464.9 | 107.5 | 30.0 | 8.6 | 63.8 | 1839.6 |
| continuation | 61.5 | 70.4 | 15.2 | 3.7 | 44.2 | 1249.9 |
| five | 923.3 | 676.2 | 200.6 | 55.3 | 320.4 | 2330.5 |

| Case | Lowest sampled available RAM (GiB) | Highest sampled swap used (GiB) |
|---|---:|---:|
| tiny | 46.37 | 0.00 |
| s5 | 44.83 | 0.00 |
| s4 | 40.23 | 0.00 |
| s3 | 36.51 | 0.00 |
| continuation | 39.69 | 0.00 |
| five | 8.65 | 3.05 |

These are observations from a shared desktop host, not hardware requirements or an
isolated benchmark. The replay starts with swap already used by prior work; the
values are total host use, not memory attributed solely to this case.

## Review corrections and architecture delta

`ExperimentController.serve()` now waits on completion signals set by the actual
computation and presentation worker `finally` paths. It retains ownership through
repeated interruption and propagates the primary error after drainage. Real
subprocess SIGINT tests cover initial calculation, accepted continuation work and
blocked presentation.

Generated `run.sh` invokes `python -m tools.performance` with the generated root.
That entry pins one completed publication, including the recursive generation
performance reference. The regression executes the generated script against a
valid prefix followed by an unfinished JSON suffix. Optional reporting failures
remain isolated. Independent network export likewise pins one publication across
its readers and uses formal history coverage rather than a lagging PhysicalWorld
page. Its regression advances publication during export and retains a stale page.

Coordination policy construction, injection, getters, exports, provenance and
extension template are removed. Planner/PlanTransition directly derive the existing
semantic dependencies; explicit dependency values, indexed codecs, structural
checks and Compiler lowering remain. Dependencies order events inside a Transition;
they do not choose its timestamp. The introductory list has seven primary Model
roles; ZoneMembership remains replaceable as a lower-level optional capability.
No Plan value format, execution timestamp or default routing algorithm changed.

The [complete architecture vector](../architecture/system-architecture.svg) and its
single [editable source](../architecture/system_architecture.py) show containment,
calls and value transfer separately. [Overview](../architecture/overview.md),
[Model/state flow](../architecture/models-and-state.md) and the designated
[design contracts](../development/README.md) explain the handoffs locally. Historical
interactive demos retain their original labels and evidence.

## Focused verification

The review/shutdown/export/Performance regression run passed 57 tests, including
five new regressions (three real SIGINT positions, generated Performance execution
and advancing export). The affected Model/semantic/Compiler/Tool run passed 263;
example/default/completed-range checks passed 34, and input assembly/main-chain
checks passed 32. These runs overlap and are not a unique-test total. The example
run retained ten existing Python warnings about forking a multithreaded process.
The late Traffic correction passed 120 affected tests with one existing opt-in live
test skipped; the separately authorized full live campaign is recorded above.

Import boundaries and documentation links/anchors were checked. All 19 current
Mermaid diagrams rendered using pinned Mermaid 10.9.1 and Firefox 154.0.1. The full
architecture SVG was inspected at readable scale in three viewport sections;
its final text-overflow check was empty. Browser checks of the retained Tiny output
covered PhysicalWorld, Zone, Planner, Performance, aggregate navigation, BPF Compiler
and current Traffic flow selection, transaction details, seek and playback.

The first live browser smoke invocation exposed an obsolete Traffic camera/language
expectation in `check_inspection_visuals_browser.py`. Its Traffic check now exercises
the current flow replay, lazy packet details, frame seek and playback; it also handles
selected flows without packet rows. The maintained check passed on Tiny's retained
live output. The original failed attempt and subsequent results are preserved in
`checks/`. This verification-only change does not alter runtime or generated views;
historical demos remain unchanged.

Timing/resource observations include ordinary desktop work and optional presentation;
there was no isolated performance benchmark. Runtime success, observed Traffic
classification and intended test coverage remain separate results. The request-level
unexplained losses and unclassified windows above retain their original classifications.
