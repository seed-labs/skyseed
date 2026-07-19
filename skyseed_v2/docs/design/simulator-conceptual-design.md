# Simulator Top-Level Conceptual Design

## Status and purpose

This record separates the implemented Simulator Slice 1 contract from the
approved but still conceptual directions for later slices. It is not an
instruction to create placeholder types.

The status labels used here are:

- **implemented**: present in the current repository and accepted by the
  completed slices;
- **approved direction**: an accepted ownership, responsibility or dependency
  direction that later slices must start from;
- **conceptual/open**: a useful future concept whose final name, shape,
  visibility or detailed contract still requires investigation and approval;
- **retired/corrected**: an earlier description that no longer represents the
  approved direction.

Only the first status describes current code. `Simulator`,
`SimulationDefinition`, `SimulationTimeline`, `Topology` and the immutable
Simulator-side static domain tree are implemented and public from
`skyseed_v2.simulator`. `Planning`, every Slice and Planner, position and
connection models, the dynamic `ExecutionCompiler`, `ExecutableBundle` and
`SimulationRunner` are not implemented or frozen public contracts.

The canonical top-level boundary map is in
[`../architecture/conceptual-class-diagrams.md`](../architecture/conceptual-class-diagrams.md#simulator-future-conceptual-boundary-map).

## Implemented foundation

The following foundation is **implemented**:

- `NodeId`;
- `SatelliteSimulationInput`, `GroundStationSimulationInput` and
  `SimulatorStaticInput`;
- the entity-aware functions in `simulator_input_generation.py`;
- `SimulationDefinition`, `SimulationTimeline`, `Simulator`, `Topology` and
  Simulator-owned `SkyseedAutonomousSystem`, `Shell`, `Orbit`, `Satellite` and
  `GroundStation` static domain objects;
- the static Emulator domain model;
- `SkyseedDockerCompiler`, which lowers that static model to Docker/Compose;
- `ComposeProjectLauncher`, which launches and verifies an already compiled
  Compose project.

The `*SimulationInput` values are receiving-side boundary contracts. Their
package placement does not make them the Simulator's complete or long-lived
domain model.

## Emulator to Simulator construction boundary

Slice 1 implements this boundary direction:

```text
Emulator entities
→ simulator_input_generation
→ immutable boundary data
→ Simulator construction
→ Simulator-owned domain objects
```

`SimulatorStaticInput` and the related `*SimulationInput` values carry
immutable static facts from Emulator to Simulator. They do not represent the
complete internal network world after construction. `Simulator.create_topology()`
uses the input to atomically materialize a distinct immutable domain graph.
Static-input node queries accept only complete `NodeId` values; no local string
ID scan or fallback exists.

The input-generation adapter alone understands both Emulator entities and
Simulator input types. The input values retain no Emulator entity, SEED Node,
Registry, container, namespace or Compose reference. Simulator core does not
call back into the Emulator or depend directly on
`simulator_input_generation.py`.

The following shape is explicitly not the future direction:

```text
Simulator
└── one increasingly large SimulatorInput
    ├── satellites
    ├── orbits
    ├── timeline
    ├── policies
    └── every other concern
```

`SimulatorStaticInput` and `create_simulator_static_input(...)` are the frozen
public names. No `EmulatorStaticInput` or `create_emulator_static_input(...)`
compatibility alias exists. The distinct Simulator-side Shell, Orbit,
Satellite and GroundStation types are now implemented; they do not replace or
subclass the Emulator entities with the same local names.

## Simulator composition root and facade

The implemented Slice 1 composition is:

```text
Simulator
├── SimulationDefinition
│   ├── SimulatorStaticInput
│   └── SimulationTimeline
├── Topology
└── future Planning (not implemented)
```

`Simulator` is the Simu-side composition root and user-facing facade. It:

- consumes exactly one `SimulationDefinition`;
- retains that exact Definition and exposes its exact Timeline;
- creates and retains at most one `Topology` only after an explicit
  `create_topology()` call;
- exposes `get_definition()`, `get_timeline()`, `create_topology()` and
  `get_topology()`.

Before creation, `get_topology()` fails. A second creation fails. If input
materialization fails, assignment has not happened, so the Simulator retains
no partial Topology. Planning is not present in Slice 1.

`Simulator` is not merely a data packet retaining an `input` field. It
materializes and owns its domain graph. It also does not personally implement:

- orbit propagation;
- Zone geometry;
- connection-candidate discovery or connection selection;
- route calculation;
- Slice comparison;
- semantic or backend planning;
- backend-operation compilation;
- dynamic execution.

The implemented constructor is `Simulator(definition: SimulationDefinition)`.
It imports neither Emulator/SEED types nor the external generation adapter.

## SimulationTimeline ownership

`SimulationTimeline` has a peer lifetime to Topology from the Simulator
facade's perspective, although the exact instance is retained inside the
immutable Definition. It is not owned by Topology or by an individual Slice.
Its implemented fields are:

- `start_time`;
- an optional `end_time`;
- `slice_interval`;
- a bounded or continuing time range.

The value is frozen, validates a positive interval and matching timezone
awareness, reads no current clock, converts no timezone, and yields timestamps
lazily. `end_time` is inclusive when bounded; `None` is unbounded.

Topology creation in Slice 1 does not consume timestamps and does not create a
Slice. A later single-instant Topology entry remains conceptual, for example:

```text
Topology.create_slice(timestamp, previous_slice?)
```

A continuous Slice sequence is therefore Simulator coordination over the
Timeline and repeated calls to the single-instant Topology entry. The method
name and parameters are illustrative, not frozen API. Continuing execution,
lookahead, sliding windows and stopping mechanisms remain **conceptual/open**.

## Topology aggregate and facade

Topology is the aggregate root for the implemented Simu-owned static network
world. Future dynamic topology state remains conceptual. The current ownership
tree is:

```text
Simulator
└── Topology
    └── SkyseedAutonomousSystem
        ├── GroundStation
        └── Shell
            └── Orbit
                └── Satellite
```

All enumeration properties return tuples. Every private mapping is an
immutable `MappingProxyType`, and enumeration and lookup paths return the same
object instances. Orbit owns shared altitude, inclination and right ascension;
Satellite owns only `node_id`, `orbit_index` and `mean_anomaly_deg`.

Topology itself keeps only `_autonomous_systems`,
`_autonomous_systems_by_asn` and `_nodes_by_id`. `get_satellite()` and
`get_ground_station()` use the unified node index and fail closed on a wrong
node kind. `get_shell()` delegates through `get_autonomous_system()`, and
`get_orbit()` delegates through the AS and Shell owners. There is no global
Shell/Orbit index, duplicate typed node index, Satellite-to-parent reverse
index, `get_shell_for_satellite()` or `get_orbit_for_satellite()`.

Future Zone definitions, topology states and comparison behavior may belong
under this aggregate only after their own approved slices. Topology must remain
a thin coordination shell rather than accumulate propagation, connection,
routing and comparison algorithms. A future outer facade and internal
implementation may intentionally expose similar operations:

```text
Planning
→ Topology.compare_slices(left, right)
→ internal TopologyDiffer.compare(left, right)
```

There must be only one authoritative comparison algorithm. The Topology
operation delegates to it so callers do not depend on internal objects or data
structures. Whether an independently named `TopologyDiffer` exists is
**conceptual/open**.

## Planning dependency on Topology

The dependency is one-way:

```text
Topology
→ produces TopologySlice / TopologyDifference

Planning
→ consumes Topology public results
→ may query the Topology read-only facade when necessary
```

Topology does not depend on Planning. Planning neither owns, copies nor
modifies Satellite, Orbit, Shell, GroundStation, Zone definitions or Topology
indexes.

The preferred information path is:

```text
TopologySlice A + TopologySlice B
→ Topology.compare_slices(...)
→ TopologyDifference
→ Planning
```

Information necessary to describe the change belongs in
`TopologyDifference`, expressed through stable logical identities such as
`NodeId`, Zone IDs and Connection IDs. Slice 1 currently provides these
read-only static facade query forms:

```text
Topology.get_node(node_id)
Topology.get_satellite(node_id)
Topology.get_ground_station(node_id)
Topology.get_autonomous_system(asn)
Topology.get_shell(asn, shell_id)
Topology.get_orbit(asn, shell_id, orbit_id)
```

They return the immutable domain objects. Later slices must prove whether they
are sufficient for a real Planning consumer; they must not add speculative
reverse indexes merely for convenience. Planning must not access Topology
private dictionaries; depend on raw `SimulatorStaticInput`; recompute
Topology; or change an authoritative future state to accommodate backend
execution cost.

The core boundary is:

```text
Topology decides what the authoritative network state should be.

Planning decides how to transition from one authoritative state to another.
```

## Two planning levels

The following chain is an **approved direction**, while all names and fields
remain **conceptual/open**:

```text
TopologyDifference
→ SemanticPlanner
→ SemanticTransitionPlan
→ BackendPlanner
→ BackendExecutionPlan
```

`Planning` is the coordinating shell; its internal Planners own the concrete
algorithms. `SemanticTransitionPlan` describes backend-neutral network
meaning. `BackendExecutionPlan` describes one backend's operations,
dependencies, batching, barriers, serial/parallel relations, failure behavior
and cleanup behavior.

BPF and Linux MPLS execution plans may have fundamentally different forms.
A BackendPlanner must not recompute Topology or disguise failure through a
different-backend fallback.

## Static deployment and dynamic execution

The two chains have different inputs, outputs, lifecycles and execution
semantics.

The **implemented** static deployment chain is:

```text
Emulator / SEED static model
→ SkyseedDockerCompiler
→ compiled Docker / Compose project
→ ComposeProjectLauncher
```

`SkyseedDockerCompiler` compiles the static deployment environment.
`ComposeProjectLauncher` launches and verifies the compiled project; it is not
an Emulator execution service or a dynamic-simulation executor.

The **conceptual/open** dynamic execution chain is:

```text
BackendExecutionPlan
→ ExecutionCompiler
→ ExecutableBundle
→ SimulationRunner
```

`ExecutionCompiler` only lowers an already determined backend plan.
`SimulationRunner` only executes the resulting bundle. Neither stage may
re-plan, recompute Topology, select another backend or hide a failure with a
fallback path.

`SkyseedDockerCompiler`, `ComposeProjectLauncher`, `ExecutionCompiler` and
`SimulationRunner` are external application/infrastructure services acting on
domain models or planning results. Emulator and Simulator do not own them as
domain state. A future Simulator facade may invoke them without changing that
ownership.

No common `Compiler`, `EmulatorRunner` or generic `Runner` abstraction is
approved. Such an abstraction would require later independent evidence; name
symmetry alone is insufficient.

## Open questions after Slice 1

The first vertical slice has frozen only construction, time rules and the
immutable static Topology graph. Later work still requires its own fact,
design, contract and review phases.

Later design must still decide:

- precise Timeline, timestamp, Slice identity, retention and comparison
  contracts;
- continuous operation, lookahead, sliding-window and stopping semantics;
- Zone representation and geometry delegation;
- the internal propagation, connection, route, index and comparison objects;
- whether Topology queries return entities or dedicated read-only views;
- exact semantic and backend plan schemas and supported backends;
- `ExecutableBundle` contents and dynamic-execution resource resolution;
- final names, visibility, module placement and root exports.

No Zone, Slice, connection, route, Planning, dynamic compiler, executor or
runtime path is implemented or authorized by Slice 1.
