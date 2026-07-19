# Simulator Top-Level Conceptual Design

## Status and purpose

This record synchronizes the approved top-level boundary before the first
Simulator vertical-slice investigation. It is not an implementation
specification, public API freeze or instruction to create placeholder types.

The status labels used here are:

- **implemented**: present in the current repository and accepted by the
  completed slices;
- **approved direction**: an accepted ownership, responsibility or dependency
  direction that later slices must start from;
- **conceptual/open**: a useful future concept whose final name, shape,
  visibility or detailed contract still requires investigation and approval;
- **retired/corrected**: an earlier description that no longer represents the
  approved direction.

Only the first status describes current code. In particular, `Simulator`,
`SimulationTimeline`, `Topology`, `Planning`, every Slice and Planner, the
dynamic `ExecutionCompiler`, `ExecutableBundle` and `SimulationRunner` are not
implemented or frozen public contracts.

The canonical top-level boundary map is in
[`../architecture/conceptual-class-diagrams.md`](../architecture/conceptual-class-diagrams.md#simulator-future-conceptual-boundary-map).

## Implemented foundation

The following foundation is **implemented**:

- `NodeId`;
- `SatelliteSimulationInput`, `GroundStationSimulationInput` and
  `SimulatorStaticInput`;
- the entity-aware functions in `simulator_input_generation.py`;
- the static Emulator domain model;
- `SkyseedDockerCompiler`, which lowers that static model to Docker/Compose;
- `ComposeProjectLauncher`, which launches and verifies an already compiled
  Compose project.

The `*SimulationInput` values are receiving-side boundary contracts. Their
package placement does not make them the Simulator's complete or long-lived
domain model.

## Emulator to Simulator construction boundary

The boundary data has this **approved direction**:

```text
Emulator entities
→ simulator_input_generation
→ immutable boundary data
→ Simulator construction
→ Simulator-owned domain objects
```

`SimulatorStaticInput` and the related `*SimulationInput` values carry
immutable static facts from Emulator to Simulator. They do not represent the
complete internal network world after construction. The future Simulator will
use satellite, orbit, Shell and GroundStation facts, together with user
parameters, to materialize domain structures owned by its own subsystems.

The input-generation adapter alone understands both Emulator entities and
Simulator input types. The input values retain no Emulator entity, SEED Node,
Registry, container, namespace or Compose reference. A future Simulator must
not call back into the Emulator or depend directly on
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

Whether `SimulatorStaticInput` should later be renamed, and whether Simu
should introduce distinct Shell, Orbit, Satellite and GroundStation types, are
**conceptual/open** questions for the first vertical slice. This synchronization
does not rename code or reserve replacement names.

## Simulator composition root and facade

The top-level ownership relation is an **approved direction**:

```text
Simulator
├── SimulationTimeline
├── Topology
└── Planning
```

`Simulator` is the Simu-side composition root and user-facing facade. It will:

- consume the boundary data and user parameters required for construction;
- create and own one `SimulationTimeline`;
- create and own one `Topology`;
- create and own one `Planning` subsystem;
- coordinate their lifecycles;
- expose a small set of stable top-level operations.

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

The exact constructor, facade operations, lifecycle and module placement are
still **conceptual/open**.

## SimulationTimeline ownership

`SimulationTimeline` is owned by `Simulator` beside `Topology`; it is not owned
by Topology or by an individual Slice. It conceptually expresses:

- `start_time`;
- an optional `end_time`;
- a default Slice interval;
- a bounded or continuing time range.

Topology creates state for one explicit instant through the conceptual entry:

```text
Topology.create_slice(timestamp, previous_slice?)
```

A continuous Slice sequence is therefore Simulator coordination over the
Timeline and repeated calls to the single-instant Topology entry. The method
name and parameters are illustrative, not frozen API. Continuing execution,
lookahead, sliding windows and stopping mechanisms remain **conceptual/open**.

## Topology aggregate and facade

Topology is the aggregate root for the Simu-owned static network world and
dynamic topology state. Its approved ownership direction is:

```text
Simulator
└── Topology
    ├── Shell
    ├── Orbit
    ├── Satellite
    ├── GroundStation
    ├── future Zone definitions
    ├── future TopologySlice states
    └── internal models and indexes
```

The names and representations of the nested objects remain open, but their
information has one authoritative owner or manager: Topology. This corrects
the earlier description that kept Zone definitions independent of Topology.

Topology is a thin coordination shell. It establishes its own static domain
structures, exposes stable query operations, coordinates the internal objects
that actually own data or algorithms, creates and manages future
`TopologySlice` states, and provides the future Slice-comparison facade. It
must not become a mega-class containing all propagation, connection, routing
and comparison algorithms.

An outer facade and an internal implementation may intentionally expose
similar operations:

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
`NodeId`, Zone IDs and Connection IDs. When planning genuinely needs stable
static facts, it may use conceptual read-only facade queries such as:

```text
Topology.get_node_kind(node_id)
Topology.get_satellite(node_id)
Topology.get_orbit(orbit_id)
```

Those methods are examples, not frozen contracts. Later slices must decide
whether such queries return domain entities or dedicated read-only views.
Planning must not access Topology dictionaries, indexes or nested objects;
depend on raw `SimulatorStaticInput`; recompute Topology; or change an
authoritative `TopologySlice` to accommodate backend execution cost.

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

## Open questions and next investigation

When formally started, the first vertical slice's fact phase will investigate
the materialization relationship among Simulator, Topology and the
Emulator-to-Simulator boundary data. It may then inspect relevant prior
behavior in a targeted way. This record authorizes neither that investigation
nor implementation.

Later design must still decide:

- whether existing input types are renamed;
- whether Simu uses distinct Shell, Orbit, Satellite and GroundStation types;
- the smallest stable Simulator construction and facade contracts;
- precise Timeline, timestamp, Slice identity, retention and comparison
  contracts;
- continuous operation, lookahead, sliding-window and stopping semantics;
- Zone representation and geometry delegation;
- the internal propagation, connection, route, index and comparison objects;
- whether Topology queries return entities or dedicated read-only views;
- exact semantic and backend plan schemas and supported backends;
- `ExecutableBundle` contents and dynamic-execution resource resolution;
- final names, visibility, module placement and root exports.

No Simulator implementation, placeholder, public export, compiler, executor or
runtime path is created or authorized by this documentation synchronization.
