# Current implementation state

Skyseed v2 is on the Round-B atomic Network Planner cutover. The current tree
has one intended production chain:

```text
SimulationTimeline
→ PhysicalWorld / PhysicalFrame
→ PhysicalLinkObservation
→ Planner / PlannedOperationalState
→ Plan
→ BPF Compiler / BpfCompiledRealization
→ BpfRunner / RealizedRuntimeState
```

The public Planner, immutable operational values, self-contained timestamped
Plan, transition replay, 50-second planning horizon, 20-second publication
horizon, Compiler source boundary, incremental realization producer, and
persisted-only runtime handoff are the current contracts. The Simulator owns
one PhysicalWorld and one Planner; experiment orchestration owns the producer;
the BPF backend owns compilation and runtime realization.

The former semantic slicing and Topology-as-physical-owner paths are retired
completely. They are neither fallbacks nor alternate production routes.

## Canonical behavior

Canonical S3 remains unchanged: 720 Satellites, 355 GroundStations, 732 Zones,
a 10-minute Timeline at 5-second resolution, K3 cross-plane candidate scope,
2+2 ISL policy, maximum 4 ISLs per Satellite, and a 20-second acquisition
reference. The publication contract yields 121 formal transitions, 30 Plans,
30 compiled realizations, and 121 activation timestamps.

The maintained canonical entry is
`starlink_gen1_satellite_internet`; it preserves the full scenario rather than
substituting a smaller fixture.

Each Plan replays against the preceding published state and carries continuous
Plan/world/state lineage. `compile_bpf` consumes only the Plan, exact
PhysicalWorld, and previous compiled realization. Empty transitions preserve
formal time.

## Architecture delta: focused performance correction

The accepted domain chain and ownership boundaries are unchanged. The
correction removes four redundant representations/work paths inside those
boundaries: semantic precedence Cartesian products already implied by Runner
ordering, the realization's duplicate source Plan, the Planner publication's
second internal apply replay, and uncompressed/repeated planning evidence.
Planner retains that publication result only as its own next-prefix state;
Producer carries only the published Plan, and Compiler independently replays it
against the preceding realization. Compiler now
records compact fold counts and only materializes exact fallback constraints;
the handoff owns runtime intentions and final intended state while Planner
history remains the authoritative Plan source for Traffic and detached Tools.
Runner activation and lifecycle semantics are unchanged.

## Maintained execution mode

Precomputed execution drains the one incremental producer, writes compact
physical/planning/compiler evidence, and persists deterministic checksum-
verified realization records. A terminal domain-history manifest seals exact
Timeline cadence, counts, frontiers, lineage, and all three stream hashes;
detached readers reject incomplete or mixed evidence. The compact Compiler
stream is not the complete realization: the independent checksum handoff owns
the planned/intended payload, and `runtime/execution.json` anchors its hashes.
Before Runner construction the complete handoff is eagerly decoded and
structurally lineage-validated; Runner pulls only validated tuple entries. The
Planner history is streaming gzip evidence with indexed precedence endpoints,
while each realization keeps only timestamp/fingerprint activation provenance.
Semantic precedence is folded onto existing Runner frontiers, phases, and atomic
forwarding generations, with explicit pairwise fallback only where required.
GroundStation planning consumes the sparse feasible observation directly,
reuses immutable PhysicalWorld capacity indexes, and solves one global indexed
sparse capacitated assignment while preserving the accepted rank, Satellite
order, and successive-shortest-path tie identity.
The future
live mode is a separate qualification stage: the same
producer plus a bounded concurrent coordinator and Runner consumer.

## Accepted offline performance baseline

The accepted normal S3 offline generation completed in 847.79 seconds external
wall time. Within that qualified run, runtime packaging fell from 380.484 to
17.932 seconds, Planner Tool generation from 315.473 seconds / about 4.11 GB to
218.610 seconds / about 546 MB, and Compiler Tool generation from 351.112
seconds / about 8.46 GB to 0.640 seconds / about 462 MB. Detached candidate
capture was 10.911 seconds and domain-evidence persistence was 26.650 seconds.
These are retained milestone conclusions, not promises for a future runtime
deployment.

## Qualification boundary

Round-B completion requires focused, integration, full-suite, documentation,
import, browser, and canonical offline S3 evidence. Those exact results belong
in the external implementation evidence directory and completion report, not
as hard-coded claims in this living status document.

Offline compilation is not full runtime qualification. The full S3 persisted
Runner/Traffic deployment has not yet been rerun after Round B. It remains the
next dedicated qualification after independent source review and focused
correction.

The earlier synchronous rolling failure is historical performance evidence,
not a failure of the accepted persisted-only architecture. The next milestone
is new-architecture runtime/performance qualification: establish a formal
post-cutover S3 Runner and Traffic baseline first, then investigate a bounded
Producer/Compiler-to-Runner rolling pipeline and later larger-scale
qualification.

automatic rollback and a public dataplane plugin API are deferred.
realtime/live traffic visualization is also deferred and is not implied by
Round B.

## Authoritative references

- [accepted design](accepted-design.md)
- [system overview](human-readable/system-overview.md)
- [planning contracts](codex-reference/planning-contracts.md)
- [Compiler contract](codex-reference/bpf-compiler.md)
- [producer and persisted runtime](codex-reference/producer-runtime-contracts.md)
- [public imports](codex-reference/public-imports.md)
- [inspection guide](user-guides/inspection-tools.md)
