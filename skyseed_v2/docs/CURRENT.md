# Current implementation state

## Offline preparation closeout (2026-09-14, pending independent review)

Three later private changes follow the completed full-scale lifecycle below:

- Explicit Compose retry reconciles project-wide `network_mode`, `ipc` and `pid`
  service dependencies against actual retained owner IDs. Missing, failed or
  replaced owners invalidate transitive consumers across groups. Affected existing
  consumers are removed before owners and recreated in dependency order; unrelated
  healthy containers retain their identities. Configuration/image checks, real
  stop hooks/grace, stage deadlines and partial failure evidence remain.
- Initial IPv4 collision inventory uses each retained namespace's `_RouteSocket`
  for local addresses/prefixes and all-table non-default routes. Namespace identity,
  complete dump/error handling, collision rejection, concurrency and initialization
  cleanup ownership remain. There is no command fallback.
- The private v4 reader directly constructs eager typed operations from the packed
  record, avoiding the recursive generic operations tree. Short text and scoped
  target reuse are capped at 16384 entries each and released with the artifact.
  Sealed-file APIs, writer bytes, integrity/format checks, exact ordering/scopes,
  shared first/successor preparation, complete READY and fixed clock remain.

Evidence is separate from all preserved live/interrupted runs and normal outputs:
`/home/lionheart/workspace/skyseed-local/experiments/offline-closeout-20260914/`.
Representative diagnosis was followed by exactly one matched sequential before/
after pass over the existing successor's 61 boundaries, 732789 operations and
481043017 bytes. The original decoder is frozen in `baseline_runner_artifact.py`;
`before.json`, `after.json`, their per-boundary JSONL and `matched-comparison.json`
record the comparison. Both passes reproduce every sealed record byte-for-byte
and produce identical derived activation/generation/connection digests.

| Measured scope | Before | After |
|---|---:|---:|
| Successor loading/validation | 13.374 s | 12.992 s |
| Full boundary decoding | 99.138 s | 42.706 s |
| CPU route-plan/activation-request construction | 33.650 s | 34.572 s |
| Peak process RSS (`ru_maxrss`) | 791.176 MiB | 752.199 MiB |

Decoding is 56.9% shorter; decoding plus construction is 41.8% shorter despite the
0.922-second construction increase. The same CPU-only predecessor setup is outside
these intervals (79.064/77.203 seconds); semantic proof work is also separate
(57.080/50.362 seconds). Only the terminal predecessor build/current state and
current boundary scratch are retained. Peak RSS includes setup, mapped file pages,
construction and proof scratch; it is neither decoder-only memory nor the full
container deployment. Both artifacts release all identity caches on close.
Construction excludes kernel prewrite, telemetry assembly and live channel waits.
This is a single offline comparison, not a new live READY margin or full-scale
memory qualification. The historical live 84.534 seconds is not its baseline.

One current-namespace read-only Netlink/`ip -j` comparison matched all three local
address facts and nine non-default route facts with the namespace inode unchanged.
Its 0.000618/0.266398-second query times do not establish a 4763-node audit speedup.
No new network resources, live qualification, full generation, matrix or visual
inspection were performed. S5's unobserved Zone-to-GS transition remains an accepted
sparse-coverage limitation; the original Traffic WARN and losses remain visible.

Validation: 428 affected offline tests passed, one existing single-realization
fixture case skipped (52.90 seconds). The suite covers Compose retry/failure,
Netlink framing/semantics, v4 equivalence/corruption/cache lifetime, scoped planning,
Compiler/Runner contracts, continuation/READY, preparation/capacity, Producer,
Traffic and nonvisual Tool replay. Environment and import-dependency checks,
the full documentation check and `git diff --check` passed. Documentation verified
92 authoritative Markdown files, 69 Mermaid diagrams, 261 exports and 124 owner
contracts. All Python checks used `skyseed_dev`, Python 3.12.13 at
`/home/lionheart/miniconda3/envs/skyseed_dev/bin/python` (pip 26.1.2); the existing
VS Code Node executable ran functional replay tests. Logs are retained in the
offline evidence root. Earlier overlapping focused suites are not added to 428.

### Architecture Delta

These three closeout changes add no public types, methods, attributes, ownership,
parent relations, module dependencies, exports, top-level directories, cross-layer
contracts, formal execution paths, class diagrams or Example call-chain changes.
`_PackedBoundaryReader` and its bounded artifact-owned reuse are private BPF decode
mechanics; IPv4 queries belong to the existing retained socket, and retry remains
the Compose deployment utility's responsibility.

The cumulative checkpoint also includes the previously approved scoped-shell
contracts below: `network_scope` and Zone `service_key` on existing semantic values,
Model constructor scope/quota inputs, scoped `BpfCompiledRealization.zone_prefixes`
and corresponding evidence/Tool versions. Their class/member documentation and
Example model configuration are synchronized. No public type/export, owner,
dependency direction or formal owner chain was added. The user authorized one
local checkpoint, excluding the unchanged PPT discussion edits in
`docs/candidates/README.md` and `docs/candidates/paper-discussion.md`; no push or
subsequent tuning campaign belongs to this closeout.

## Historical whole-Shell lifecycle and preparation run (2026-09-13/14)

The approved implementation replaces the A/B/C campaign and count curves with
whole metadata-defined deployment groups, fixed configurable startup concurrency
8, and reverse group cleanup through one native Docker CLI removal queue at a
time. Partial deployment failure retains confirmed matching groups for explicit
retry; actual service dependencies, hooks and graceful shutdown settings remain.
Startup and cleanup carry one overall stage budget each.

Runner preparation carries destination deltas with reference counts through route
derivation, maintains interface collision/capacity proofs incrementally, and shares
static telemetry identities and unchanged node route projections. Initial and
successor deliveries use one incremental decode/construct/prewrite path. Main phase
compatibility checks, fixed runtime admission, kernel-result checks, version reuse
and safe reclamation remain. This scenario sets lead fraction 1.0 and capacity
factor 2; READY still requires complete preparation before the fixed deadline.

The interrupted campaign remains intact at
`/home/lionheart/workspace/skyseed-local/experiments/shared-gs-scopes-20260911T040523Z/`.
Its A run failed at the 300-second READY deadline. Recovery on 2026-09-13 directly
verified all 4765 recorded containers as stopped and project-owned, then removed
them in reverse whole groups and verified the containers and owned network absent
in approximately 115 seconds. This recovery is not the new live qualification.
New evidence is under
`/home/lionheart/workspace/skyseed-local/experiments/shell-lifecycle-20260913/`.
The single full live lifecycle qualification completed. Generation including five
generation Tools took 2220.532 seconds; startup with independent verification took
663.686 seconds (generated script 646.859); run including runtime Tools took
1479.475 seconds. All stayed within the approved overall ceilings. Both delivery
manifests and binaries match the retained corrected campaign byte for byte.
The qualified source fingerprint is
`f1c03955d24438db74a61b0f4857f37c965eccf69bda53fa5f8efa655651297c`.

Preparation/B0 took 337.129 seconds. The successor took 239.537 seconds in total:
15.071 loading, 84.534 decoding and 139.931 construction/prewrite. READY preceded
the fixed 300-second seam by 60.458 seconds. One B0 and all 120 Main activations
completed through 600 seconds; all 121 timestamps and the fixed clock origin were
verified. Runner cleanup completed in 51.358 seconds. Durable Runner history and
all 121 telemetry generations are complete with zero dropped/lost events.

Generated deployment cleanup removed all 4765 containers and the owned network
in 306.662 seconds. The external verifier's unprivileged host pin-root stat then
failed with PermissionError; its original FAILED/UNPROVEN records are preserved.
A read-only check using an existing image confirmed that exact host pin root
absent, and its temporary verification container was verified absent. Final owned
container/network/helper/pin-root absence was confirmed 392.405 seconds from the
original cleanup start, within the 1800-second ceiling. No removal was rerun.
See `REPORT.md`, `qualification-evidence-summary.json` and
`final-owned-resource-confirmation.json` in the external evidence directory.

Traffic execution and its Tool completed, but the Tool reports WARN / coverage
NOT PROVEN: S5 lacks an observed Zone-to-GS transition request. There are two
unclassified replay observations; raw executor loss is 12/1345 (0.892%). No Traffic
infrastructure or persistent-flow failure was reported. Runner Tool completed and
Observation passed. The user accepts S5's missing transition observation as sparse
coverage, without a repair or further qualification requirement. Complete Traffic
coverage is not claimed.

The retained 64/8 and 128/8 startup scripts took 1897.588 and 1160.279 seconds;
the new script took 646.859. Retained attempt A's Preparation/B0 took 693.255
seconds and failed at the seam. These observations have different source/host
conditions; 64/8 predates scoped semantics. No new baseline or per-mechanism causal
speedup is claimed. The prior 232.446-second construction/prewrite number excluded
75.033 seconds of decoding.

Focused checks passed (150 passed / 1 skipped; later affected checks 101 passed;
final deployment checks 17 passed, overlapping suites). Environment, import,
documentation and whitespace checks passed in Conda `skyseed_dev`, Python 3.12.13.
Normal outputs and the interrupted campaign are preserved. No additional live
attempt, commit/push or user visual acceptance was part of that qualification.

Architecture Delta for this round: none in public types, methods, attributes,
ownership, parent relations, module dependencies, exports, top-level packages,
cross-layer contracts, class diagrams or Example call chain. Changes are private
algorithms and generated deployment controls within the approved owners. Existing
scoped semantic contracts below belong to the pre-existing uncommitted work.

## Shared-GS scoped planning implementation (2026-09-11)

Zone service identity is `(network_scope, zone_id)`. The empty scope retains
unscoped behavior. The five-Shell example explicitly supplies satellite scope
membership to the GS, Zone and Route Models: every shared GS reserves two
preparing-or-operational slots per Shell (ten total), every geographic Zone has
one explicit assignment per Shell, and the geographic three-GS association is
applied separately in each scope. The scoped Route Model excludes other-scope
satellites and GS transit. No cross-Shell communication Model is implemented.
One Compiler/Runner owns shared interfaces, forwarding and cleanup. Distinct
scoped Zone IPv4 destinations prevent shared-GS forwarding collisions. Physical
feasibility, satellite capacities and preparation/handover rules still apply;
association does not promise an operational path. Isolation is configured in
Models, not imposed as a permanent PhysicalWorld rule.

The self-contained Plan remains the only semantic Compiler input. Offline scoped
qualification and full generation-data checks have passed. The interrupted historical A/B/C live
campaign is retained under
`skyseed-local/experiments/shared-gs-scopes-20260911T040523Z/`; normal outputs below
remain historical evidence. At that stage no commit, push, assistant visual
acceptance or defaults promotion was authorized; the later checkpoint authorization
is recorded above.

Current contracts use Plan/transition v3, planned state v2, domain history v3,
Runner artifact v4, Planner Tool v4, Compiler Tool v5 and Traffic Tool v7. Older
Runner/domain artifacts are rejected rather than assigned new scoped semantics.
The five-Shell example has 732 geographic Zones, 3660 scoped services and 10980
scoped Zone–GS pairs. Other examples retain empty-scope behavior. The new generated-control strategy is described above; historical 64/8 and
128/8 startup records are retained for comparison.


## Historical accepted optimization milestone

The user and independent Work review previously accepted the cumulative Producer,
multi-Shell, Runner and Tools milestone on `feature/skyseed-v2`. The retained
closeout records below describe that earlier source and output state; they do not
authorize publication, archive deletion or any additional action in this task.

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

## Retained pre-scope implementation and qualification

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
- The historical persisted artifact v3 supported append-only finite deliveries with fixed nodes,
  one Preparation/B0 and one clock. Main owns activation and joined physical
  deletion. Bounded backend Maintenance loads/prewrites successors and reclaims
  eligible retired data; a missed READY deadline fails explicitly. Per-node content
  versions reduce repeated BPF writes. No generation or online planning runs in Main.
- Generated startup keeps configurable defaults of 64 services / eight concurrent
  requests at every resident count. Cleanup retains exact immutable IDs, reverse
  dependencies, disposable fast removal and explicit graceful settings/hooks;
  its default batches are eight IDs at/above 2000 remaining, then 16 below.
- The historical Planner presented 2196 recorded stable Zone–GS pairs, preserving
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

## Historical validation and Architecture Delta

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

Cross-Shell communication and coordinated geographic GS/Zone optimization remain deferred.
The scoped example prevents cross-Shell allocation competition but does not promise full slots or reachability.

Example restructuring, the comprehensive README/Mermaid rewrite, legacy V1 removal,
Skyseed 1.0 naming and publication to `seed-labs/skyseed` belong to a later round.
No commit or push is part of the current scoped implementation task.

The incremental compute producer still feeds `compile_bpf` and persisted artifacts.
The unscoped `starlink_gen1_satellite_internet` S3 has 720 Satellites and produces
four Plans at the default 32-frame publication size across 121 timestamps.
No automatic rollback, public dataplane plugin API, or realtime/live traffic visualization
is introduced by this scoped implementation.
