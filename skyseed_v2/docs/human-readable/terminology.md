# Terminology

| Term | Current meaning |
|---|---|
| SimulationTimeline | finite cadence-aligned formal timestamp sequence with required start/end and one positive observation interval; end is an inclusive bound |
| PhysicalWorld | authoritative static physical aggregate and requested-time projection owner |
| PhysicalFrame | exact timestamp plus complete immutable positions |
| PhysicalLinkRequest | Planner-created bounded candidate scope for one physical evaluation |
| PhysicalLinkObservation | sparse feasible physical candidates and work counts for one frame/request |
| physical candidate | feasible relationship fact; never automatically operational |
| selected | candidate chosen by operational policy for lifecycle advancement |
| acquiring | selected ISL whose acquisition reference has not elapsed |
| operational Connection | planned usable relationship with effective parameters |
| PlannedOperationalState | complete operational Connections, Zone assignments, and Route outcomes at one timestamp |
| PlanTransition | replay-complete semantic delta for exactly one formal timestamp; may be empty |
| Plan | immutable contiguous group of transitions with Plan/world/state lineage and precedence |
| planning frontier | latest fully planned formal timestamp |
| publication frontier | latest formal timestamp included in a published Plan |
| semantic event reference | deterministic transition-derived event identity |
| CoordinationPolicy | precedence-only policy over semantic event references |
| BpfCompiledRealization | compiled intended state and backend intentions for one Plan boundary |
| activation boundary | compiled record for one formal transition, including a no-op transition |
| sealed Runner artifact | backend-owned finite typed packed runtime input, with terminal manifest, identity/hash seals and capacity demand |
| Preparation | one-time infrastructure initialization and full first-delivery decoding, construction and semantic prewrite, ending with physical B0 activation |
| delivered batch | finite sealed append-only execution coverage, carrying predecessor identity; independent of internal Plan publication size |
| Maintenance | existing Runner/backend background role for bounded batch loading/prewriting and eligible runtime reclamation; never activation authority |
| READY | complete admitted delivery prepared before its nominal activation deadline |
| resident reservation | fixed plan/generation/map capacity derived from initial delivery and configured admission factor |
| Main | execution of the prepared current-boundary delta; formal run begins at B1 |
| generation publication | forwarding selection subphase inside a boundary; it does not prove whole-boundary completion |
| formal ACTIVE frontier | latest completely executed boundary, including withdrawals and all joined physical deletion |
| RealizedRuntimeState | Runner-owned observation of live runtime state |
| incremental realization producer | sole Timeline cursor and frame-to-observation-to-Plan-to-Compiler orchestrator |
| precomputed execution | sealing finite execution deliveries before use, without runtime semantic planning |
| planned unreachable | explicit Route outcome such as Zone unserved or no operational path |
| unexplained reachable loss | runtime failure while the compiled planned outcome says reachable |
| detached Tool | read-only consumer of durable records that cannot affect semantic compute or Runner artifact validity |

Physical, planned, compiled intended, and realized truths are never synonyms.
Likewise, candidate, selected/acquiring, operational, routed, compiled, and
realized are distinct lifecycle meanings.

Logical endpoint ports and live ifindex bindings are private BPF representations,
not domain Port entities. Prepared records, capacity accumulators, retained
execution resources and helper-owned retirement workers/ledger remain private
Runner/backend mechanics. Asynchronous identity writing is observation work.
These create no public domain owner or independent network lifecycle; current-boundary deletion finishes inside Main.

| Current computation term | Meaning |
|---|---|
| `PhysicalContactWindow` | physical Model proof interval; finite end is coverage, not disappearance; absent end is analytical all-future proof |
| preparation / `acquisition_duration` | Planner-owned selected reservation at both endpoints, before useful operational service |
| `minimum_service_duration` | forward-looking useful service required after preparation; not an elapsed-selection wait |
| `GroundStationLifecycleEvidence` | detached selected/pending/operational GS facts; no lifecycle authority |
| frame batch | internal publication envelope, initially 32 formal frames; no independent lookahead wait or time quantization |
| relative delay threshold | absolute drift divided by last effective delay; equality updates and rebases; zero reference is explicit |
| materials ready | required finite runtime inputs and controls sealed; optional Tools may still be running |

The former 20/50-second publication windows and fixed integer target persistence
are superseded policies retained only in explicitly historical records.

## Network scope

`network_scope` is a Model-configured network identity. In the five-Shell example
it is S1–S5. `(network_scope, zone_id)` distinguishes independent service for one
geographic Zone. It does not duplicate a GS or own lifecycle. The empty scope
preserves unscoped examples; see the [planning contracts](../../src/skyseed_v2/simulator/planning/README.md).
