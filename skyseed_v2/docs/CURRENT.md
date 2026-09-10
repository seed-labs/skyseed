# Current implementation state

## Accepted optimization milestone

The user and independent Work review have accepted the cumulative Producer,
multi-Shell, Runner and Tools milestone on `feature/skyseed-v2`. This closeout
synchronizes documentation, retains the latest normal Example outputs, removes
obsolete local experiment archives, and commits/pushes to the existing backup
repository `EMLionHeart/skyseed-dev-backup`. No new experiment, tuning or visual
QA is part of closure. Remote branch equality is verified after push.

The accepted production chain remains:

```text
SimulationTimeline
→ PhysicalWorld / PhysicalFrame / PhysicalLinkObservation
→ Planner / PlannedOperationalState / Plan
→ BPF Compiler / BpfCompiledRealization
→ sealed Runner artifact
→ BpfRunner / RealizedRuntimeState
```

Simulator owns the PhysicalWorld and Planner; experiment orchestration owns the
incremental realization producer; the BPF backend owns compilation and runtime
realization. Compose remains an optional deployment utility. Ownership and
contract details are maintained in [accepted design](accepted-design.md).

## Adopted implementation

- The finite producer advances one Timeline, computes conservative contact proofs,
  applies admission/preparation and relative parameter memory, and publishes
  32-frame Plans. Material readiness precedes optional detached Tools. Derived
  geometry, candidate, allocation, route and Compiler indexes reduce repeated work
  without introducing a second source of semantic truth.
- Current Starlink examples use K1 cyclic ISL neighborhoods and 2+2 targets,
  five-second ISL/GS preparation, 180/60-second minimum useful service, a 10%
  relative delay threshold and 120-second refresh. GS allocation optimizes coverage,
  distinct satellites, total connections, continuity and deterministic quality.
  Exact retained-flow residual optimization preserves those five objectives;
  equal-objective ties and their downstream differences remain recorded.
- Persisted artifact v3 supports append-only finite deliveries with fixed nodes,
  one Preparation/B0 and one clock. Main owns activation and joined physical
  deletion. Bounded backend Maintenance loads/prewrites successors and reclaims
  eligible retired data; a missed READY deadline fails explicitly. Per-node content
  versions reduce repeated BPF writes. No generation or online planning runs in Main.
- Generated startup keeps configurable defaults of 64 services / eight concurrent
  requests at every resident count. Cleanup retains exact immutable IDs, reverse
  dependencies, disposable fast removal and explicit graceful settings/hooks;
  its default batches are eight IDs at/above 2000 remaining, then 16 below.
- Planner primarily presents 2196 recorded stable Zone–GS pairs, preserving
  selection across serving handovers and resolving both directional outcomes
  independently. Current-serving Shell/Orbit filters, raw demand details and bounded
  lazy shards retain evidence distinctions. Traffic primarily replays recorded
  episode/probe identities across segments, with play/pause, speed, seek and distinct
  request/reply markers. Schematic animation preserves gaps and ambiguity; planned
  overlays remain separate. Frame cache hits invalidate obsolete asynchronous loads.

Mechanisms, configuration rationale and measured limits remain in the
[optimization explanation](human-readable/optimization-decisions.md),
[decision history](provenance/optimization-decision-history.md) and
[performance comparisons](human-readable/qualification-performance.md).

## Retained outputs and qualification boundaries

| Example | Retained normal output | Actual retained state |
|---|---|---|
| S1–S5 | [Tool portal](../examples/starlink_multi_shell_satellite_internet/output/inspection/index.html) | Latest successful runtime plus offline refreshed Tools; Traffic WARN. |
| S3 | [Tool portal](../examples/starlink_gen1_satellite_internet/output/inspection/index.html) | Latest default generation, five generation Tools; no attached runtime qualification. |
| Mini | [output directory](../examples/mini_satellite_internet/output/) | Historical partial output; no complete Runner binary or current Tool portal. |
| Complete static teaching Example | [maintained entry](../examples/complete_static_emulator/README.md) | No normal output currently present; none was generated during closure. |

Every existing normal `output/` retains its inputs, generated materials, records,
domain history, runtime evidence and Tools in its actual state. Outputs stay
Git-ignored and future normal runs may replace them. Latest-run measurements
formerly held externally now live in
[the multi-Shell output's qualification directory](../examples/starlink_multi_shell_satellite_internet/output/qualification/README.md);
S3 generation measurements live in its
[qualification directory](../examples/starlink_gen1_satellite_internet/output/qualification/README.md).
Selected historical summaries remain there for comparison; the obsolete raw
experiment bundles, failed/correction runs, duplicate snapshots, rebuild copies,
temporary logs and aggregate portals were deleted. Original paths inside copied
measurement records are historical provenance, not live links or dependencies.
Closure removed 224927 regular files across the five reviewed experiment
archives, their obsolete index and the superseded export: 66602549248 allocated
bytes (62.028 GiB). It retained 95 compact measurement/check files plus 14
necessary frozen executable-source overrides in normal output. The after-cleanup
hash check preserves 36092 existing files; only Mini's retention metadata changed.
The previously committed [Runner evidence archive](provenance/runner-optimization-20260907.md)
and maintained provenance are preserved.

The latest [separate visualization delivery](/home/lionheart/workspace/skyseed-local/exports/focused-correction-20260909T170209Z/)
is preserved unchanged outside Git; its superseded export was removed.

The successful session is `20260910T044347.546519766Z-154590`, with frozen runtime
source fingerprint
`641359dbff07ebb50e8e03a4de437f59022fe2b3f24650cf215478e2c1b986a4`.
It covers all five Shells, 121 formal timestamps, one B0 and 120 formal Main activations.
Materials were ready in 1406.245 s; deployment took 1901.866 s; Preparation/B0
300.780 s. Main work p50/p95/max was 0.738/1.090/1.346 s; successor readiness
preceded its deadline by 58.062 s. Original scopes and performance samples are
retained, not attributed to later source changes.

Traffic remains **WARN**: 1321 sent / 1236 received / 85 loss, including 59 expected
unreachable, three supported handover disturbances, 12 unexplained reachable
losses and 11 unclassified requests. Packet evidence separately has 28302
observations, 1253 transactions and 1240 endpoint deliveries. The units are not
forced to agree. All 20 selected coverage goals were observed; that does not
prove universal reachability, zero loss or unrestricted scaling.

Final full-scale teardown was **manually accelerated**: the current normal batch
finished, the old loop stopped, then 1661 remaining containers were force-removed
in batches of at most eight after 3104 normal removals. Owned resources were
verified absent. This is not uninterrupted default teardown qualification.
Subsequent cleanup defaults have only focused command tests and one earlier
isolated smoke; no new full-scale qualification occurred.

Current Tools were refreshed offline after the runtime. Their individual
`tool-refresh.json` records and portal `offline-tool-refresh.json` distinguish
Tool source from frozen runtime source. The later refresh preserved qualification,
transaction classifications and original spatial/path assets. User/Work acceptance
does not turn nonvisual checks into assistant-performed visual QA.

## Validation and Architecture Delta

Completed qualification is reused: 860 offline tests with two skips at the runtime
source, followed by 138 finishing tests and 119 presentation tests across their
recorded scopes (the final affected renderer recheck had 49 passes). These counts
are not additive or a newly run full suite. The presentation audit checked 265716
pair/frame states independently in both directions. Closure performs bounded
output dependency/hash checks, retained local-link and provenance checks,
documentation/import checks and `git diff --check` in `skyseed_dev`:
`/home/lionheart/miniconda3/envs/skyseed_dev/bin/python`, Python 3.12.13, pip 26.1.2.
No dependency installation, container experiment, image/page inspection or visual
gate is part of closure.

**Architecture Delta: closure — none.** Closure changes no public type/method,
important domain attribute, owner, parent relation, production dependency, root
export, top-level package, cross-layer contract, execution path, class diagram or
Example call chain.

The **cumulative accepted milestone** has a non-empty delta relative to the prior
Runner checkpoint: `PhysicalContactWindow` and `GroundStationLifecycleEvidence`;
`FrameBatchPlanningWindowPolicy` replacing the time-horizon policy;
`CoverageFirstGroundStationAccessPlanningModel` replacing quality-first selection;
contact/admission and relative parameter contracts; `Planner.flush()`,
`BpfRunner.append_batch(Path)`, lead/capacity controls and sealed continuation
metadata. Raw Traffic v2 and full Tool projection v6 preserve their aligned
producers/consumers; browser-only metadata remains private. The S1–S5 Example
assembles existing owners. Owner diagrams, package member diagrams and relevant
Simulator exports are synchronized; no independent lifecycle authority is added.

## Deferred work

Global unique Zone serving-satellite selection and shared GS admission can select
incompatible disconnected Shell components. Current ISLs are within Shells and
GS transit is forbidden, so geographic Zone–GS association alone cannot guarantee
bilateral reachability. This is a **Model coordination limitation** awaiting future
work, not a presentation defect corrected by this milestone.

Coordinated Zone/GS selection and cross-Shell communication research remain deferred.
Per-Shell serving/routing contexts over shared GS entities and Zone geometry are
only a recorded fallback alternative, not an adopted design. Shared GS physical
capacity must never be duplicated. No routing, Model or topology change is
introduced by this closeout.

Example restructuring, the comprehensive README/Mermaid rewrite, legacy V1 removal,
Skyseed 1.0 naming and publication to `seed-labs/skyseed` belong to a later round.
This submission uses only the existing backup repository and branch.
