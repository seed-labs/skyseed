# Stage 7 Acceptance: Canonical Cutover

Status: **PASS — CANONICAL STRUCTURAL CUTOVER ACCEPTED**

The canonical four-horizon real run completed successfully from freshly built
inputs. Satellite Internet 1.0 now has one implementation root under
`skyseed_v1/`; the old runner routes are retired. Later performance and entry
UX work remains subject to cheap regression gates and a user-operated real run.

## Accepted run

- run ID: `s4-v1-structural-cutover-fresh2-20260712T124412Z`;
- run directory:
  `/home/seed/skyseed_runs/validation_profiles/s4-v1-structural-cutover-fresh2-20260712T124412Z`;
- wall time: approximately 1 h 41 m 45 s;
- process return code: 0;
- final outcome: `RICH_S4_OPERATIONAL_CLEAN_PASS`;
- final external residue: zero owned containers, Compose networks, direct
  interfaces, BPF pins, locks, and processes.

The accepted command was:

```bash
env -u PYTHONPATH skyseed-v1 run \
  --example s4_operational_clean \
  --run-root /home/seed/skyseed_runs/validation_profiles \
  --run-id s4-v1-structural-cutover-fresh2-20260712T124412Z \
  --format json
```

This was a fresh normal-path preparation, not a load of the historical r139
prepared bundle. An earlier frozen-input attempt correctly failed closed after
path relocation changed its bound identity. The first fresh attempt then
exposed a migrated application-local variable error before Compose launch; it
was fixed and covered by regression tests before the accepted run.

## Runtime equivalence evidence

- 100/100 real BPF-owned epochs completed; H0-H3 each contributed 25;
- all 100 runtime transactions succeeded;
- three successor horizon process workers became ready and all three handoffs
  were consumed at the correct boundary;
- 75/75 worker-prepared inactive generations were present and 99 steady
  transitions used preloaded flip-only activation;
- no runtime full attach/reload, Linux G/Z fallback, classifier miss blocker,
  runtime failure, or hidden repair route was observed;
- rollback, Compose down, and postflight completed successfully.

After completion, the derived normal-path audit initially emitted three false
blockers because it still read pre-compaction artifact paths. The immutable run
evidence itself was complete. The reader now supports the current externalized
queue, rich timing readiness, top-level cleanup scope, standalone coverage,
and worker-ready artifacts. PASS is also fail-closed whenever a normal-path
`*_BLOCKER` remains. The real run's derived artifacts were regenerated from
those immutable sources and now contain zero blocker labels.

## Time and cost audit

Major accepted-run phases were:

| Phase | Wall time |
|---|---:|
| preview | 129.51 s |
| frozen horizon preparation | 646.16 s |
| normal runtime input build | 141.96 s |
| input externalization | 93.67 s |
| Compose build | 1355.36 s |
| Compose up | 430.74 s |
| host prewarm | 466.53 s |
| pre-runtime BPF preparation | 495.07 s |
| runtime cleanup | 288.38 s |
| Compose down | 442.61 s |
| postflight | 6.83 s |

For the 100 epochs, the reported steady transition mean was 7.55 s, p95
15.13 s, and maximum 20.18 s. The mean improved about 3.3% versus validated
r140, while p95 and maximum tails regressed about 10.8% and 16.3% respectively.
H2 contained the largest mutation spike. Activation lag averaged 25.57 s and
peaked at 53.79 s, substantially better than r140's 244.89 s mean; the legacy
`deadline_miss` field still reports all epochs against a strict zero-lag
definition even though strict activation deadlines are disabled.

The audit also found a measurement and hot-path defect hidden by the old
summary: base epoch-record construction happened synchronously after
`transition_wall_seconds` stopped. It added about 105.07 s across the run and
was mislabeled as nonblocking post-apply work. Unscheduled epoch validation
also read global BPF counters twice per epoch, contending with worker map
preload and producing no meaningful packet delta. Post-acceptance code now:

- skips the paired counter reads when no epoch ping validation is scheduled,
  while retaining final canary, background ping observation, scheduled probe,
  and failure-diagnostic evidence;
- removes the duplicate rich readiness traversal from each operational-clean
  epoch summary;
- records explicit counter-read timings when validation is scheduled.

These optimizations are regression-tested but intentionally have not triggered
another assistant-operated long run. The next real run is user-operated.

## Canonical cutover and gates

- new tiered suite: 49/49 pass (including Python composition,
  standalone-project, visualization-interface, and generated-profile gates);
- migration regression suite: 791/791 pass after deleting tests for the retired
  implicit operational-profile route;
- package compilation and `git diff --check`: pass;
- old runner files and temporary flat-module aliases are absent;
- package source has zero imports of migrated flat implementation modules;
- horizon workers launch `skyseed_v1.runtime.maintenance_worker` as a module;
- the S4 profile and reusable data are example/package owned and preserve the
  baseline content contracts;
- SEED is the clean official `master` checkout at commit
  `61acdf4c9699b25b06843ec4aad747108b05924e`;
- prepared-consumer replay remains 100/100 with 1005 binding references;
- shared G/Z generation replay remains 100/100 and exercises generation wrap;
- native BPF cold-cache compilation and the pre-long v17 contract pass;
- the post-run preflight is clean. Low free disk space is a warning to address
  before another expensive run, not evidence of runtime residue.

The primary human entry is now
`python skyseed_v1/examples/s4_operational_clean/run.py`; the same file exposes
`inspect`, `preflight`, and `preview`. The long parameter CLI remains supported
for automation.
