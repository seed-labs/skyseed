# Simulator Planner contracts

## Authority

Normative detail under `../accepted-design.md`. Implementation state is
recorded only in [`CURRENT.md`](../CURRENT.md).

## Scope

Simulator-owned semantic planning in which `Planner.synchronize()` requests
new adjacent `TopologyDifference` values from its exact sibling `Slicer`, then
consumes them to publish one immutable backend-independent `Plan`.

## Ownership and lifecycle

```text
Simulator → create_topology() → create_slicer(models)
→ optionally create_planner(algorithms)
→ create Slice state and synchronize Planner as facts become available
```

Simulator owns at most one Planner and binds it to its own exact sibling
Slicer. Topology, Slicer, and Planner are peer-level components inside the
Simulator domain. Planner does not advance Slicer, generate Slice values,
compare Slice values independently, call RouteModel, or orchestrate continuous
execution; during synchronization it asks Slicer to create each newly available
adjacent Difference from retained Slice values.
Planner retains the five injected Policy/Model role references, owns its
private continuous planning state and current published Plan, and references
the sibling Slicer. Plan owns coverage, three transition tuples, Forwarding
precedences and its temporal index; Model inputs do not become owner objects.

Planner creation requires the exact sibling Slicer but no real Slice or
Difference. Creation and later `synchronize()` discover available Differences
by calling that Slicer over retained Slice `0..N`.
PlanningWindowPolicy first selects the planning window, then a non-empty
publication window from its prefix. Models and Coordination see the full
planning window; the Plan contains only its publication window. Later
`synchronize()` extends or consumes the not-yet-published suffix without
replaying settled history. A Planner may exist before Slice 1; `get_plan()`
then fails until a Difference is available.

## Plan and transition contract

Plan records `start_slice_id`, `start_timestamp`, `end_slice_id`, and
`end_timestamp`, three independent typed immutable transition tuples, minimal
Forwarding precedences, and one immutable temporal index. Index and precedence
entries reference exact transition objects in those tuples. The index provides
deterministic inspection at exact nominal timestamps and never creates fake
offsets. Tuple position does not establish backend/runtime ordering.

Plans use continuous non-overlapping `(start, end]` coverage. A transition on a
shared boundary belongs to the earlier Plan exactly once. A
`ForwardingTransitionPrecedence(predecessor, successor)` is the only explicit
Planner statement that one otherwise same-time Forwarding transition must
precede another. Plan owns the relation, the Forwarding Model produces it
through Planner, and future approved consumers such as Compiler may consume it.
It is neither an execution schedule nor a general dependency framework;
unrelated same-time transitions remain unconstrained.
For a direct new-path dependency, downstream readiness precedes upstream
activation/use. For direct old-path teardown, the upstream REMOVE or path
switch precedes removal of the downstream state it formerly used. Mixed
changes combine only those justified relations and must remain acyclic.

Connection transitions are ESTABLISH or WITHDRAW. ESTABLISH carries the
complete formal Connection and its initial directional parameters; no duplicate
ParameterTransition is emitted for those initial values. WITHDRAW carries the
Connection identity and no Connection payload.

ParameterTransition is a sparse directional target on a planned active
Connection. ForwardingTransition preserves RoutePatch identity, operation and
next hop while adding nominal semantic time; it does not carry a route choice,
full path or backend operation.

Nominal time may be any UTC simulation timestamp inside the source Difference
interval. It is semantic planning time, not runtime completion time.

## Replaceable algorithms

| Role | First implementation | Boundary |
|---|---|---|
| `PlanningWindowPolicy` | `AllAvailablePlanningWindowPolicy` | select one exact planning-window prefix from available Differences and one non-empty publication-window prefix |
| `ConnectionPlanningModel` | sample-boundary and midpoint models | receive planning-window/affected ranges and reusable transitions; retain exact add/remove evolution, estimate time, mark matched short lifetimes as candidates |
| `ParameterPlanningModel` | accumulated absolute deadband | receive planning-window/affected ranges, reusable transitions, and immutable observed/planned baselines; compare observed values with last planned target |
| `ForwardingPlanningModel` | dependency-aware formal-path planning | receive planning-window/affected ranges, reusable transitions, and narrow immutable formal path identities; produce transitions plus required same-time precedences; never receive Slicer or invoke RouteModel |
| `CoordinationPolicy` | `DefaultCoordinationPolicy` | receive all three Model results and narrow route facts; return coherent transition collections and decisions without replacing fixed validity |

Minimum dwell only identifies a candidate. Planner gives CoordinationPolicy
matched public transition pairs, all three typed Model results, and immutable
formal route/path mappings. The policy—not private hard-coded Planner logic—may
retain them or jointly suppress the lifetime, related parameters, and transient
forwarding states. Planner validates the returned result.

The public Model contracts deliberately expose `differences`, the complete
formal Difference sequence in the planning window;
`affected_differences`, the newly added or otherwise affected suffix requiring
incremental algorithm work; and `previous_transitions`, previously planned but
still-unpublished work available for reuse or local adjustment. The latter two
are incremental-call information, not new domain objects, owners or another
Plan. This permits cross-Difference reasoning while allowing normal tail
extensions to append without settled-history replay.

Parameter Models additionally receive immutable observed and planned
directional mappings. Observed is the latest formal Simulator/Slicer parameter
state; planned is the latest target Planner actually decided should be
realized. Accumulated deadband requires that distinction across publication
boundaries. Forwarding Models receive Planner-derived, narrow read-only
per-Slice RouteKey-to-Connection-ID path facts so they never receive Slicer,
RouteModel authority or permission to recompute Simulator state. These inputs
use existing domain values; there is no public planning-context, path-context
or candidate DTO.

## Incremental state

Planner privately retains consumed Differences, the unpublished
planning-window overlap, active planned Connections, observed parameters,
last-planned targets, compact
pending deadband ranges, current planned Forwarding state, per-Slice formal
route/path evidence, and decisions. It also retains the corresponding planned
baseline at the last published boundary. None of that state is public Plan
payload.

On a tail extension, Models receive only the affected new suffix alongside
reusable unpublished coordinated transitions and state at the end of the prior
planning window. Coordination may revise that still-unpublished overlap. After
fixed validation, Planner publishes only the publication window, advances its
private published baseline from planned transitions, and retains any later
planning-window portion.
It may rebuild outer immutable collections/index for the new Plan, but does not
replay already-settled Differences or reset to the raw Slice at a Plan boundary.

Consequently accumulated deadband continues against the last planned target,
and a transient Connection previously suppressed across a boundary is not
resurrected merely because the boundary Slice contains it. Published Plan
objects are immutable history and later future input cannot revise their
coverage or content.

## Fixed invariants

- planning-window and publication-window Differences are exact available
  identity prefixes;
- published coverage begins at the prior Plan end and is non-overlapping;
- planned Connection evolution is non-contradictory and never exceeds the
  coverage-end formal Connection identities;
- ParameterTransition occurs only while its Connection is planned active;
- ParameterTransition time lies in its source Difference interval and every
  target value equals formal Simulator-provided parameter state;
- every published ForwardingTransition retains formal path evidence;
- its resulting path dependencies are active at nominal time;
- no forwarding state still depends on a Connection when it is withdrawn;
- every retained planned forwarding state came from formal Route state;
- every precedence references two same-time Forwarding transitions in its Plan;
- built-in/reference processing failure publishes neither a new Plan nor
  consumed-Difference progress or changed Planner-owned incremental state.

## Coalescing and provenance

Planner may suppress a short Connection lifetime only when CoordinationPolicy
accepts and the final formal route state does not depend on it. Parameter
intents inside that lifetime are removed. The default policy accepts only when
each affected route returns to the same formal next hop and complete path, and
actual transient ForwardingTransitions can be removed directly. Otherwise it
conservatively retains the complete formal evolution. It never creates a replacement
ForwardingTransition. When a next hop is unchanged but its complete formal path
changes, Planner refreshes that dependency as private state rather than
publishing synthetic forwarding work.

`PlanningDecision` is concise inspection evidence retained by Planner, not
Plan payload. It identifies source Difference current Slice IDs, retained /
merged / suppressed classification, deciding algorithm and a human-readable
reason. Parameter observations below threshold are represented by one compact
pending source range; only an actual multi-Difference merge emits a decision,
so no per-sample string record is accumulated.

## Failure semantics

Wrong algorithm/value types fail with `TypeError`. Invalid coverage,
algorithm output or semantic consistency fails with `ValueError`. Duplicate
or missing lifecycle creation and Plan access before the first Difference fail
with `RuntimeError`. Planner and the stateless built-in/reference algorithms
are failure-atomic at the published and Planner-owned state boundary. The
current Protocols cannot roll back private mutations performed inside an
arbitrary third-party stateful strategy before it raises; they intentionally
do not claim that external transactional guarantee and add no public
prepare/commit/discard protocol.

## Prohibited behavior

No backend/resource fields, heterogeneous canonical transition store, fake
time offsets, route recomputation, Slicer advancement, settled-history model
replay, public planning-window/baseline/checkpoint DTO, generic dependency
graph, implicit fallback, or root-package Planner export.

## Maintenance

Update the planning owner README, Simulator lifecycle, public imports, object
and Simulator-chain diagrams, repository import map, canonical example,
focused tests and `CURRENT.md` together.
