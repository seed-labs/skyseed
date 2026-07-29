# Ownership and lifecycle

## Authority

Normative detail under `../accepted-design.md`. Implementation state is
recorded only in [`CURRENT.md`](../CURRENT.md).

## Scope

Static Emulator objects, Simulator composition, static Topology, dynamic
Slicer/results, and Compose launcher.

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
| `Simulator` | caller | caller-facing composition root | Topology/Slicer lifecycle |
| `Topology` | `Simulator.create_topology` | Simulator | Models, Slicer, Tools |
| Topology AS/Shell/Orbit/nodes | Topology materialization | nested static owner tree | Models, lookups, Tools |
| `ZoneLayout` / `Zone` | Topology materialization | Topology / ZoneLayout | membership, connection Model, Tools |
| `Slicer` | `Simulator.create_slicer` | Simulator | caller and Tools |
| `TopologySlice` | Slicer | retained by the creating Slicer | queries, paths, Difference, Tools |
| `TopologyDifference` | Slicer on request | caller | Tools / future approved consumers |
| Compose project | upstream SEED `Docker` compiler | filesystem/caller | `ComposeProjectLauncher` |

## Contract

Creation order is strict:

```text
SimulationDefinition → Simulator → create_topology()
→ create_slicer(Position, Connection, Route)
→ create Slice(s) → optionally resolve path / create adjacent Difference
```

## Invariants

- exactly one Topology and one Slicer per Simulator;
- Slicer cannot precede Topology;
- Topology objects are new receiver-owned objects, never Emulator aliases;
- each Model instance has one Slicer owner and each Model role uses a distinct
  instance;
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
