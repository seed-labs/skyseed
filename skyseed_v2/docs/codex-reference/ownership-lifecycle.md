# Ownership and lifecycle

## Authority

Normative detail under `../accepted-design.md`. Implementation state is
recorded only in [`CURRENT.md`](../CURRENT.md).

## Scope

Static Emulator objects, Simulator composition, static Topology, dynamic
Slicer/results, Compose launcher, and live BPF Runner lifecycle/inspection
handoff.

## Owners and consumers

| Object | Creator | Owner | Primary consumers |
|---|---|---|---|
| upstream `Emulator` | `create_static_emulator` or handwritten assembly | itself/composition caller | SEED render/compiler, boundary projector |
| `SkyseedAutonomousSystem` | construction or upstream Base caller | upstream Base/Emulator | Shell/GS creation, projector |
| `Shell` | Skyseed AS | Skyseed AS | Orbit construction, projector |
| `Orbit` | Shell | Shell | Satellite construction, projector |
| Emulator `Satellite` | Orbit | Orbit, plus same-reference AS extension-node lifecycle index | SEED, compiler, projector |
| Emulator `GroundStation` | Skyseed AS | Skyseed AS | SEED, compiler, projector |
| `SimulatorStaticInput` | boundary functions | calling assembly / `SimulationDefinition` | Topology materialization |
| `Simulator` | caller | caller-facing composition root | Topology/Slicer/Planner lifecycle |
| `Topology` | `Simulator.create_topology` | Simulator | Models, Slicer, Tools |
| Topology AS/Shell/Orbit/nodes | Topology materialization | nested static owner tree | Models, lookups, Tools |
| `ZoneLayout` / `Zone` | Topology materialization | Topology / ZoneLayout | membership, connection Model, Tools |
| `Slicer` | `Simulator.create_slicer` | Simulator | caller and Tools |
| `TopologySlice` | Slicer | retained by the creating Slicer | queries, paths, Difference, Tools |
| `TopologyDifference` | Slicer on request | caller | Tools / future approved consumers |
| `Planner` | `Simulator.create_planner` | Simulator | caller and future approved semantic consumers |
| `Plan` | Planner publication | retained as current immutable interval by Planner | caller and future approved semantic consumers |
| `ForwardingTransitionPrecedence` | ForwardingPlanningModel through Planner | containing Plan | caller and future approved Plan consumers such as Compiler |
| `BpfCompiledRealization` | `compile_bpf` | immutable caller/backend handoff | next compile call and BpfRunner future-work input |
| Compose project | upstream SEED `Docker` compiler | filesystem/caller | caller-selected deployment startup; optional `ComposeProjectLauncher` |
| live Compose project identity | deployment caller | caller | `BpfRunner` attachment |
| `BpfRunner` | runtime caller after deployment startup | sole live BPF lifecycle owner | caller lifecycle operations; detached post-run Inspection handoff |
| dynamic physical resources/runtime state | BpfRunner Preparation/Maintenance | creating BpfRunner | BpfRunner Main, Maintenance reclaim and scoped cleanup |
| Runner inspection snapshot | `BpfRunner.inspection_snapshot()` | receiving runtime caller as detached scalars plus bounded recent events | repository-local Runner Inspection Tool, combined with complete durable Runner JSONL |

## Contract

Creation order is strict:

```text
SimulationDefinition → Simulator → create_topology()
→ create_slicer(Position, Connection, Route)
→ optionally create_planner(planning algorithms)
→ create Slice(s)
→ Planner.synchronize() requests adjacent Difference values from Slicer
→ read the published Plan
```

Only `Topology → Slicer → optional Planner` is a construction prerequisite.
Planner may exist before the first real Slice/Difference; later Slice generation
and Planner synchronization may interleave. Slicer creates each Difference on
request without retaining it, while Planner consumes and retains only the
working/history facts its planning lifecycle requires.

The live backend handoff is separately ordered:

```text
generated output/inputs/execution.json
→ private root generated-BPF-execution application composition verifies
  source/generated-input consistency
  and restores the named progression builder
→ lazy Slicer → Planner → compile_bpf supplier
→
live Compose project identity + future BpfCompiledRealization callable
→ BpfRunner.prepare() independently discovers/validates/freezes its foundation
→ exact-next plus dual-bound continuous READY coverage
→ BpfRunner Maintenance advances input/READY frontiers and reclaims retired state
→ BpfRunner.activate_next() manually activates the exact next timestamp
  or BpfRunner.run() executes relative to one monotonic wall-clock origin
→ BpfRunner.cleanup()
→ BpfRunner.inspection_snapshot() detaches summary + frozen-binding scalars + bounded recent events
→ private root generated-BPF-execution composition persists the detached snapshot + invocation outcome
→ generated run.sh invokes the Runner Tool, which reads that handoff and complete runner-events.jsonl directly
```

Each rolling owner releases only its own obsolete working facts after a
successful downstream handoff. Complete Slice/Difference, Plan/decision,
compiled-realization/boundary, Runner-event, and traffic evidence is appended
under the corresponding `output/inspection/` owner before release. Tools are
readers of those streams and do not control the release frontier.

Runner freezes the discovered required deployment incarnation; a recreated
container or changed PID/netns is stale, never silently rediscovered at a
boundary. Runner does not own Compose teardown. Runtime active/ready frontiers
are nominal timestamps; realization Slice coverage remains provenance only. A
Runner incarnation chooses manual or automatic execution once, never both.
Rolling construction requires an explicit non-negative nominal-time lookahead
and a positive future-boundary depth. Startup counts bootstrap as the first
future boundary; after activation the depth counts only READY boundaries
strictly beyond ACTIVE. The continuous prefix stops at whichever bound is
reached first, without weakening exact-next readiness. Full-range construction
uses `None` for both bounds and requires bounded finite input.
The inspection accessor has no lifecycle side effect and performs no
Docker/kernel command; cleanup-retained frontiers are last-known history, not
a claim that active network semantics still exist.

## Invariants

- exactly one Topology and one Slicer, and at most one Planner per Simulator;
- Slicer cannot precede Topology;
- Planner cannot precede Slicer and references that exact sibling;
- Topology objects are new receiver-owned objects, never Emulator aliases;
- each Slicer Model instance belongs to its exact Slicer; approved Planner
  algorithm roles may be implemented by the same object when all Protocols are
  satisfied;
- Slice publication is atomic after all three results validate.

## Failure semantics

Wrong types fail with `TypeError`; duplicate lifecycle creation and missing
prerequisites fail with `RuntimeError`; missing owner lookups fail closed with
`KeyError`; invalid values/order fail with `ValueError`.

## Prohibited behavior

No global object registry, cross-owner lookup service, mutable owner transfer,
Model fallback, or hidden lifecycle creation.

## Extension points

Approved Protocol implementations may supply Zone membership and the three
Models. They do not change owners.

## Maintenance

Update owner tests, package README, human-readable chains, public imports, and
architecture dependency checks.
