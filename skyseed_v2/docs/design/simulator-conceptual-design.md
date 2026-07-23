# Simulator Top-Level Conceptual Design

## Status and purpose

This record separates the implemented, accepted/frozen Iteration 1 contract
and implemented Iteration 2 static Zone contract from the accepted/frozen but
not implemented Dynamic Slice contract and deferred conceptual directions.
It is not an instruction to create placeholder types.

The status labels used here are:

- **implemented**: present in the current repository and accepted by the
  completed slices;
- **approved direction**: an accepted ownership, responsibility or dependency
  direction that later slices must start from;
- **accepted/frozen design, not implemented**: an approved public contract
  that does not yet describe current code or exports;
- **conceptual/open**: a useful future concept whose final name, shape,
  visibility or detailed contract still requires investigation and approval;
- **retired/corrected**: an earlier description that no longer represents the
  approved direction.

Only the first status describes current code. `Simulator`,
`SimulationDefinition`, `SimulationTimeline`, `Topology`, the immutable static
node tree and the static Zone domain are implemented and public from their
owning modules. Position, Connection, Route, Slicer, TopologySlice and
TopologyDifference are accepted/frozen design but not implemented. Planning,
Plan, DifferenceWindow, the dynamic `ExecutionCompiler`, `ExecutableBundle`
and `SimulationRunner` remain deferred and unimplemented.

The normative sources are
[`../architecture/accepted-design.md`](../architecture/accepted-design.md),
the detailed
[`../architecture/dynamic-slice-public-contract.md`](../architecture/dynamic-slice-public-contract.md)
and [`../CURRENT.md`](../CURRENT.md) for implementation status. The canonical
boundary map is in
[`../architecture/conceptual-class-diagrams.md`](../architecture/conceptual-class-diagrams.md#simulator-approved-dynamic-slice-boundary-map).

## Implemented foundation

The following foundation is **implemented**:

- `NodeId`;
- `SatelliteSimulationInput`, `GroundStationSimulationInput` and
  `SimulatorStaticInput`;
- the entity-aware functions in `simulator_input_generation.py`;
- `SimulationDefinition`, `SimulationTimeline`, `Simulator`, `Topology` and
  Simulator-owned `SkyseedAutonomousSystem`, `Shell`, `Orbit`, `Satellite` and
  `GroundStation` static domain objects;
- `LatitudeLongitudeRectangleDefinition`, `ZoneDefinition`,
  `ZoneLayoutDefinition` and `load_zone_layout_definition`;
- `LatitudeLongitudeRectangle`, `Zone`, `ZoneLayout`, `ZoneMembershipModel`
  and `DefaultZoneMembershipModel`;
- the static Emulator domain model;
- `SkyseedDockerCompiler`, which lowers that static model to Docker/Compose;
- `ComposeProjectLauncher`, which launches and verifies an already compiled
  Compose project.

The `*SimulationInput` values are receiving-side boundary contracts. Their
package placement does not make them the Simulator's complete or long-lived
domain model.

## Emulator to Simulator construction boundary

Iteration 1 implements this boundary direction:

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

Iteration 2 adds a separate construction input that converges before
materialization:

```text
zones.yaml / programmatic construction
→ ZoneLayoutDefinition
→ Simulator.create_topology(...)
→ Topology-owned ZoneLayout
→ Zone[]
→ fixed ZoneMembershipModel
→ Topology facade
```

The file path is `raw YAML mapping/list →
LatitudeLongitudeRectangleDefinition → ZoneDefinition →
ZoneLayoutDefinition`. YAML is only one input source. The loader directly
returns `ZoneLayoutDefinition`, and there is no YAML-specific Zone type.
Programmatic and file-driven paths therefore meet before entering Simulator.
Definitions are materialization input and are not retained by Topology.

## Simulator composition root and facade

The implemented composition through Iteration 2 is:

```text
Simulator
├── SimulationDefinition
│   ├── SimulatorStaticInput
│   └── SimulationTimeline
├── Topology
│   ├── static node tree
│   └── ZoneLayout [0..1]
└── future Planning (not implemented)
```

`Simulator` is the Simu-side composition root and user-facing facade. It:

- consumes exactly one `SimulationDefinition`;
- retains that exact Definition and exposes its exact Timeline;
- creates and retains at most one `Topology` only after an explicit
  `create_topology(*, zone_layout_definition=None,
  zone_membership_model=None)` call;
- exposes `get_definition()`, `get_timeline()`, `create_topology()` and
  `get_topology()`.

Before creation, `get_topology()` fails. A second creation fails. A Model
cannot be supplied without a `ZoneLayoutDefinition`. If any static-node or Zone
materialization step fails, assignment has not happened, so the Simulator
retains no partial Topology. Planning is not present.

`Simulator` is not merely a data packet retaining an `input` field. It
materializes and owns its domain graph. It also does not personally implement:

- orbit propagation;
- the Zone geometry or membership algorithms retained by `ZoneLayout`;
- connection-candidate discovery or connection selection;
- route calculation;
- Slice comparison;
- semantic or backend planning;
- backend-operation compilation;
- dynamic execution.

The implemented constructor is `Simulator(definition: SimulationDefinition)`.
It imports neither Emulator/SEED types nor the external generation adapter.

## Static Zone domain and ownership

The implemented object tree is:

```text
Simulator
└── Topology
    ├── SkyseedAutonomousSystem[]
    │   ├── GroundStation[]
    │   └── Shell[]
    │       └── Orbit[]
    │           └── Satellite[]
    └── ZoneLayout [0..1]
        ├── Zone[]
        │   └── LatitudeLongitudeRectangle
        └── ZoneMembershipModel
```

Topology owns at most one `ZoneLayout`; omitting a layout is valid. The layout
is the sole owner of its Zone tuple and immutable ID index, and both paths
reference the same Zone instances. Topology stores the layout and delegates
`get_zone_layout()`, `get_zone()`, `find_zones()` and `resolve_zone()` to it. It
does not retain `ZoneLayoutDefinition`.

The active Model is selected during materialization and cannot be replaced
after Topology creation. It is a calculation strategy retained by the layout,
not a second owner or copy of the Zone collection. Reusing one Definition for
two Simulators materializes two independent Topology/ZoneLayout/Zone object
trees.

The implemented responsibilities are deliberately narrow:

- `Zone` carries one identity, priority and rectangle and delegates
  `contains(...)` to that rectangle;
- `ZoneLayout` owns the collection, coordinates queries and validates all Model
  results at its boundary;
- `ZoneMembershipModel` is the replaceable physical-membership and
  authoritative-resolution algorithm Protocol;
- `Topology` is the public read-only facade.

The actual call chains are:

```text
Topology.find_zones(...)
→ ZoneLayout.find_zones(...)
→ active ZoneMembershipModel.find_zones(...)
→ Zone.contains(...)

Topology.resolve_zone(...)
→ ZoneLayout.resolve_zone(...)
→ active ZoneMembershipModel.resolve_zone(...)
```

The Model is rules and algorithms, not an entity, configuration file model,
Zone owner, Satellite selector, previous-state holder or Connection, Route or
Planning component. `DefaultZoneMembershipModel` has no fields and is
stateless. A third-party Model is passed the layout-owned Zone tuple for
membership and the validated physical-match tuple for resolution. The layout
wraps any `TypeError` raised while invoking or tuple-materializing custom
`find_zones` as its public iterable-result `TypeError`; other membership
exceptions propagate. It rejects a foreign Zone, a duplicate Zone and an
authoritative result that is not one of the physical matches. Exceptions
raised by custom resolution behavior otherwise propagate. Determinism, no
previous state and no service-Satellite behavior are extension contracts;
Python's `Protocol` cannot enforce the absence of a third-party object's
internal mutable state.

## Physical membership and authoritative resolution

Physical membership means every Zone for which the point satisfies closed-
boundary `contains`. An authoritative Zone is the single physical match
selected by the active Model. Thus a shared boundary can produce more than one
physical match while resolution still produces one authoritative result.

The default resolution order is:

```text
all physical matches
→ maximum priority
→ minimum haversine central angle to representative center
→ lexicographically smallest zone_id within 1e-12 radians
```

Higher priority wins and the Definition default is `0`. The rectangle's
`representative_center` is a deterministic representative midpoint, not a
spherical area centroid. Haversine produces a central angle, so no Earth radius
is needed. The `1e-12`-radian tolerance applies only to distance ties; it never
widens `contains`.

Rectangle input longitudes are in `[-180, 180]`. Input `180` canonicalizes to
`-180`, and internal longitudes use `[-180, 180)`. `west < east` is an ordinary
interval, `west > east` crosses the date line, and equal bounds after
canonicalization are invalid. There is no special all-longitude rectangle.
Containment uses closed bounds and no implicit epsilon. At exactly latitude
`+90` or `-90`, longitude is ignored when the rectangle includes that pole.

When `allow_overlap=False`, overlap is rejected exactly when both
`latitude_overlap > 0` and `longitude_overlap > 0`. A shared edge, shared
corner, single shared pole or shared date-line boundary has zero area and is
valid. Those boundary points may still be physical members of multiple Zones;
`find_zones()` preserves them and `resolve_zone()` selects one.

Zone YAML and programmatic construction enforce the same Definition failure
conditions. The YAML entry wraps failures in
`StaticEmulatorConfigLoadError`; that inherited name is configuration-boundary
debt, and Iteration 2 adds no new exception type. These two notes and the
third-party mutability limitation are non-blocking.

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

Topology creation does not consume timestamps and does not create a
Slice. The accepted Dynamic Slice contract keeps Timeline as the timestamp
authority and assigns ordered generation to Simulator-owned Slicer, not
Topology. The approved aware-only UTC normalization is not implemented at
this baseline. Retention policy remains deferred.

## Topology aggregate and facade

Topology is the aggregate root for the implemented Simu-owned static network
world. Dynamic state is accepted/frozen design but remains unimplemented and
is not Topology-owned. The current ownership tree is:

```text
Simulator
└── Topology
    ├── SkyseedAutonomousSystem[]
    │   ├── GroundStation[]
    │   └── Shell[]
    │       └── Orbit[]
    │           └── Satellite[]
    └── ZoneLayout [0..1]
        ├── Zone[]
        │   └── LatitudeLongitudeRectangle
        └── ZoneMembershipModel
```

All enumeration properties return tuples. Every private mapping is an
immutable `MappingProxyType`, and enumeration and lookup paths return the same
object instances. Orbit owns shared altitude, inclination and right ascension;
Satellite owns only `node_id`, `orbit_index` and `mean_anomaly_deg`.

Topology itself keeps `_autonomous_systems`, `_autonomous_systems_by_asn`,
`_nodes_by_id` and `_zone_layout`. `get_satellite()` and
`get_ground_station()` use the unified node index and fail closed on a wrong
node kind. `get_shell()` delegates through `get_autonomous_system()`, and
`get_orbit()` delegates through the AS and Shell owners. Zone queries delegate
to the optional layout. There is no global
Shell/Orbit index, duplicate typed node index, Satellite-to-parent reverse
index, `get_shell_for_satellite()` or `get_orbit_for_satellite()`.

The accepted Dynamic Slice contract keeps Topology as this immutable static
aggregate and query facade. Topology does not create TopologySlice or compare
Slices. Slicer coordinates Models, atomically publishes TopologySlices and
invokes the one fixed pure adjacent-Difference comparison. TopologyDifference
is independent; it is not owned by TopologySlice, Planning or a public
`TopologyDiffer`.

## Retired/non-authoritative dynamic candidates

The following earlier candidate descriptions are retained only as provenance
and cannot override the accepted anchor or detailed contract:

```text
Topology.create_slice(timestamp, previous_slice?)
Topology.compare_slices(left, right)
Topology creates TopologySlice or TopologyDifference
TopologySlice creates or owns TopologyDifference
same orbit_index defines same-Orbit ring or cross-Orbit pairing
Tool recomputes Position, Connection or Route
```

## Planning dependency on Topology

The accepted dependency is one-way:

```text
Static Topology + Timeline
→ Slicer
→ produces TopologySlice / adjacent TopologyDifference

future Planning
→ consumes Topology public results
→ may query the Topology read-only facade when necessary
```

Topology does not depend on Planning. Planning neither owns, copies nor
modifies Satellite, Orbit, Shell, GroundStation, Zone, ZoneLayout or Topology
indexes.

The preferred information path is:

```text
TopologySlice A + TopologySlice B
→ Slicer.create_difference(...)
→ TopologyDifference
→ future Planning
```

Information necessary to describe the change belongs in
`TopologyDifference`, expressed through stable logical identities such as
`NodeId`, Zone IDs and Connection IDs. The current implemented baseline
provides these read-only static facade query forms:

```text
Topology.get_node(node_id)
Topology.get_satellite(node_id)
Topology.get_ground_station(node_id)
Topology.get_autonomous_system(asn)
Topology.get_shell(asn, shell_id)
Topology.get_orbit(asn, shell_id, orbit_id)
Topology.get_zone_layout()
Topology.get_zone(zone_id)
Topology.find_zones(latitude_deg, longitude_deg)
Topology.resolve_zone(latitude_deg, longitude_deg)
```

They return the immutable domain objects. Later slices must prove whether they
are sufficient for a real Planning consumer; they must not add speculative
reverse indexes merely for convenience. Planning must not access Topology
private dictionaries; depend on raw `SimulatorStaticInput`; recompute
Topology; or change an authoritative future state to accommodate backend
execution cost.

The core boundary is:

```text
Models plus Slicer determine and publish authoritative dynamic network state.

Future Planning decides how to transition from one authoritative state to
another.
```

## Historical two-planning-level candidate

The following earlier decomposition remains **non-authoritative provenance**;
its names, levels and fields are not frozen:

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

The accepted future boundary currently freezes only:

```text
TopologyDifferenceWindow
+ explicit planning context
→ future Planner
→ future Plan
```

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

## Open questions after Dynamic Slice contract acceptance

Iteration 1 froze construction, time rules and the immutable static node graph.
Iteration 2 implements the static Zone Definition/materialization/ownership,
geometry and membership contract. The Dynamic Slice public contract now
freezes Position, Connection, Route, Slicer, TopologySlice and
TopologyDifference design, but none is implemented.

Planning/Plan schemas, DifferenceWindow implementation, backend execution,
runtime resource policy, concrete diagnostics API, final dependency/library
selection and private performance structure remain deferred.

The approved dependency labels are grouped for engineering authorization:
A–E is the complete Dynamic Slice main chain, F is TopologyDifference and G is
Tool/canonical example/final synchronization. Each group requires explicit
authorization, but A through E do not require mechanical authorization pauses
between internal dependency steps. This architecture-contract synchronization
does not authorize A–E implementation.
