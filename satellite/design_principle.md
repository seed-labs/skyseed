# Satellite Iteration 2 Design Principle

This document is the source of truth for Iteration 2 in `satellite/`. It
describes the architectural direction of the satellite research platform. It
does not prescribe a step-by-step implementation sequence.

## Project Goal

`satellite/` should evolve from a controller-driven prototype into a research
platform for experimenting with replaceable satellite-network mechanisms and
policies. Researchers should be able to swap orbit models, visibility models,
topology slicers, routing/MPLS planners, runtime update stores, runtime
installers, and update optimization methods without redefining the whole
platform around one example workflow.

## Core Architecture Principles

- Mechanism and policy must be separated.
- Platform interfaces must be stable enough to support multiple algorithms.
- Time-sliced orbit/topology generation should produce explicit snapshot
  abstractions.
- `UT` positions are fixed for the current Iteration 2 stage, but they still
  participate in visibility and topology computation.
- Zone division and zone assignment logic must stay behind a replaceable
  abstraction such as `ZoneGenerator`.
- Routing/MPLS planning must be modular and replaceable.
- Runtime agents and installers should execute generic update plans, not infer
  planning intent.
- Update transport and storage must be abstracted. `etcd` may be investigated
  later but must not be hard-coded prematurely.
- OpenSN should be investigated first and used as an architectural reference
  where appropriate, but not copied blindly.
- Iteration 2 changes should stay inside `satellite/` unless later design
  review justifies broader scope.
- Long-term runtime architecture decisions should be evaluated against eventual
  large-scale targets, not only the current small prototype.

## Mechanism And Policy Boundary

The platform should separate the following layers.

- Mechanism:
  - orbit data ingestion
  - visibility evaluation
  - time-slice topology construction
  - snapshot serialization
  - planner invocation
  - runtime update distribution
  - node-local execution
  - local Linux state installation and verification
- Policy:
  - orbit assumptions
  - visibility thresholds
  - zone generation strategy
  - topology slicing rules
  - serving-satellite selection rules
  - gateway selection rules
  - routing/MPLS algorithm choice
  - optimization heuristics for minimizing runtime churn

No runtime executor should need to understand which policy selected a gateway,
zone owner, label, or route. It should only consume a validated plan contract.

## Current Iteration Goals

### 1. Orbit-Driven Topology Generation

Introduce an orbit/visibility/topology slicing pipeline that can compute
time-sliced network topology from:

- satellite orbit data
- gateway locations
- `UT` and zone locations
- visibility constraints
- `ISL` and `GSL` availability
- zone-to-serving-satellite mapping

The result should be a stable topology snapshot abstraction that downstream
planners can consume without depending on raw orbital calculations.

For the current stage:

- each `UT` has fixed geographic coordinates during the experiment
- each `UT` therefore has stable zone membership once the zone generator assigns
  zones
- zone serving-satellite selection must not rely only on abstract zone-center
  visibility
- the responsible satellite for a zone must be selected with awareness of the
  `UT`s inside that zone so that those `UT`s can continue attaching to the same
  serving satellite instead of being immediately disconnected

The exact zone partitioning algorithm is intentionally undecided. Zone
generation must be represented behind a replaceable abstraction such as
`ZoneGenerator`, with future options including fixed latitude/longitude grids,
HEALPix-like partitioning, or user-defined zones.

For each time slice, each zone must have exactly one active responsible serving
satellite. Backup candidates or handover windows may be added later, but the
active zone egress used by the default Z-side MPLS semantics must be unique in
that slice.

### 2. Modular Routing/MPLS Planning

Define a planner interface that consumes a topology snapshot and emits a
runtime update plan.

Iteration 2 should define a default G/Z MPLS planner with these semantics:

- `G-table`:
  - `G` means gateway egress
  - mainly used for `UT`-to-Internet or `UT`-to-gateway-direction traffic
  - the `UT` side determines the target gateway or gateway-egress class from
    local destination or policy information
  - the `UT` side applies the corresponding ingress label before sending the
    packet into the satellite network
  - once labeled, the MPLS core forwards the packet along precomputed
    label-switched paths toward the selected gateway
- `Z-table`:
  - `Z` means zone egress
  - mainly used for return or network-to-`UT`-direction traffic
  - the network or `GW` side maps the destination `UT` or destination address
    to that `UT`'s zone
  - each zone has exactly one responsible serving satellite per time slice
  - traffic is label-switched toward that responsible satellite
  - the responsible satellite then delivers traffic toward the `UT` access side
    or beam abstraction

`G-table` and `Z-table` are logical planning concepts, not necessarily literal
physical tables that must exist at runtime. Intermediate satellites do not need
to understand high-level G/Z semantics. They only need node-local MPLS
forwarding behavior such as label push, swap, and pop, as generated by the
planner and installer pipeline.

This default planner is only one policy module. The architecture must allow
other MPLS planners or an IP-routing planner to replace it.

Planner output must therefore not be prematurely defined as "two runtime
tables". The planner should emit whatever `RuntimeUpdatePlan` entries are
required by the chosen Linux MPLS or runtime installer contract, such as:

- ingress classification rules
- label allocation results
- `LFIB` entries
- route rules
- cleanup or replacement rules
- validation expectations

### 3. Agent-Based Runtime Update

Move toward container-local agents that fetch or receive update plans and
install local Linux state themselves. Early versions may still use simplified
storage or polling, but the architecture must support future update stores such
as `etcd` or another backend without redesigning planner or installer
contracts.

Each container should eventually be responsible for applying only its own
node-local route, MPLS, and link-related state.

The final runtime update mechanism is not decided yet. Pull-based agents,
push-based delivery, `etcd`-backed storage, file or snapshot stores, or another
mechanism are all possible candidates. The design goal is speed, stability, and
scalability, including eventual large-scale scenarios on the order of
`10,000` satellites. Iteration 2 should avoid assumptions that only work
because the current prototype has a tiny number of containers.

### 4. Runtime Optimization Investigation

Investigate OpenSN runtime architecture and optimization ideas before
implementation. Focus areas include:

- node daemon structure
- key-value or config distribution
- avoiding excessive Docker CLI interaction
- reducing unnecessary virtual link creation
- runtime topology or link update representation

This phase is investigation-first. Implementation should wait until findings
are documented and reviewed.

## Expected Module Boundaries

Iteration 2 should move toward explicit module boundaries similar to the
following.

- `orbit model`
  - produces time-indexed satellite state inputs
- `zone generator`
  - maps fixed `UT` coordinates into stable zone membership through a
    replaceable zone-partitioning policy
- `visibility model`
  - determines candidate `ISL`, gateway, and `UT/zone` visibility per slice
- `topology slicer`
  - converts model outputs into a `TopologySnapshot`
  - assigns exactly one active responsible serving satellite per zone per slice
- `planner`
  - converts one `TopologySnapshot` into a `RuntimeUpdatePlan`
- `update store / distributor`
  - persists and exposes update plans to nodes
- `runtime agent`
  - retrieves or receives one node-local plan
- `installer`
  - applies Linux state on the node
- `validator / reporter`
  - reports success, failure, rollback, and local observed state

The exact file layout may evolve, but these responsibilities should remain
separable.

## Stable Snapshot Requirement

Orbit and topology generation must produce snapshot abstractions that are
stable, explicit, and planner-facing. A snapshot should describe what is true
for one time slice, not how the slice was derived internally. Planners should
not depend on hidden controller state or ad hoc parsing of transient runtime
files.

A `TopologySnapshot` should be able to express at least:

- fixed `UT` coordinates for the current stage
- stable `UT`-to-zone membership
- candidate and active visibility relationships
- active `ISL` and `GSL` availability
- exactly one active responsible serving satellite for each zone in the slice
- the relationships among `UT`, zone, satellite, and gateway objects that the
  planner needs

## Non-Goals For Iteration 2

- replacing the whole platform with one hard-coded MPLS implementation
- hard-coding one zone partitioning algorithm as platform behavior
- hard-coding `etcd` as the mandatory update store
- defining planner output too early as two literal runtime tables
- embedding planner decisions inside the runtime agent or installer
- modifying SEED core by default
- copying OpenSN code or structure without verified justification
- turning the current prototype CLI behavior into permanent architecture
- requiring a distributed routing control plane inside the emulation as the
  first Iteration 2 milestone

## Relationship To The Current Baseline

The current codebase already contains a controller-driven prototype, logical
constellation inventory, and an example MPLS path workflow. Iteration 2 should
reuse useful mechanisms where appropriate, but it must not let the current
default MPLS implementation define long-term platform architecture.
