# Iteration 2 Contract Boundaries

This document defines the responsibility boundaries of the three core Iteration
2 contracts:

- `TopologySnapshot`
- `RuntimeUpdatePlan`
- `NetworkMutationPlan`

The purpose of this phase is to clarify who owns each contract, who consumes
it, what it is allowed to express, and what it must not express. This document
does not define final field schemas.

## Design Goal

Iteration 2 needs stable contract boundaries before implementation begins.
Those boundaries must preserve:

- mechanism and policy separation
- planner replaceability
- runtime execution replaceability
- future performance optimization freedom

The core rule is:

- topology understanding is not forwarding policy
- forwarding policy is not physical network mutation
- runtime executors must not reverse-engineer planner or orbit logic

## 1. TopologySnapshot

`TopologySnapshot` is the stable planner-facing description of one time slice
 of the network.

### Who Generates It

- the topology slicer generates `TopologySnapshot`
- the topology slicer consumes outputs from:
  - orbit model
  - visibility model
  - zone generator
  - topology slicing rules

### Who Consumes It

- planners consume `TopologySnapshot`
- validation or reporting tools may also inspect it
- debugging tools may render it

Runtime agents, installers, or host daemons should not need to consume raw
`TopologySnapshot` in the normal runtime path.

### What It Expresses

`TopologySnapshot` expresses what is true for one time slice from a
planner-relevant topology perspective, such as:

- snapshot identity and time-slice identity
- satellite, gateway, `UT`, and zone relationships relevant to planning
- fixed `UT` positions for the current stage
- stable `UT`-to-zone membership for the current stage
- candidate and active visibility relationships
- active `ISL` and `GSL` availability
- exactly one active responsible serving satellite per zone in that slice
- planner-relevant topology state needed for route or MPLS planning

### What It Does Not Express

`TopologySnapshot` must not express:

- MPLS label state
- `LFIB` entries
- `ip route` / `ip rule` / `iptables` / `sysctl` actions
- Docker-specific mutation instructions
- namespace, veth, bridge, or `docker network` operations
- local installer command sequences
- runtime transport details such as push, pull, store, or watch mechanisms

### Boundary With Orbit / Visibility / Zone Generator

The boundary is:

- orbit model
  - computes time-indexed orbital state inputs
- visibility model
  - computes candidate visibility and link availability facts
- zone generator
  - assigns stable zone membership for the current stage
- topology slicer
  - turns those upstream results into one stable `TopologySnapshot`

This means `TopologySnapshot` is downstream of orbit, visibility, and zone
generation. It should contain the results needed by planners, not the raw
internal propagation logic that produced them.

### Boundary With Planner

The boundary is:

- `TopologySnapshot` describes topology facts and planning inputs
- planner chooses routing, MPLS, gateway-egress, zone-egress, and update
  policy outputs

The planner may reject an infeasible snapshot, but it must not require hidden
controller state outside the snapshot contract.

## 2. RuntimeUpdatePlan

`RuntimeUpdatePlan` is the logical runtime contract emitted by a planner.

### Who Generates It

- a planner generates `RuntimeUpdatePlan`
- the planner may be:
  - default G/Z MPLS planner
  - alternative MPLS planner
  - IP-routing planner
  - future custom planner

### Who Consumes It

- runtime agent consumes plan-level or node-local slices of
  `RuntimeUpdatePlan`
- container-local installer consumes installer-relevant local actions derived
  from it
- validators and reporters may consume it for post-apply checks

The host daemon should not consume `RuntimeUpdatePlan` as its primary physical
mutation contract unless a specific derived projection is intentionally defined.

### What It Expresses

`RuntimeUpdatePlan` expresses logical node-local forwarding and runtime state
that must be installed for a slice, such as:

- plan identity and target snapshot reference
- planner identity and planner kind
- node-local subplans
- forwarding or classification intent at node scope
- label allocation results relevant to runtime nodes
- local route, MPLS, rule, NAT, and related runtime expectations
- validation expectations
- replacement, supersession, cleanup, or rollback intent

### What It Does Not Express

`RuntimeUpdatePlan` must not express:

- orbit propagation details
- raw visibility calculation internals
- zone-generation internals
- host-level physical link mutation steps
- namespace or veth manipulation procedures
- Docker bridge or `docker network` mutation instructions
- planner-irrelevant controller orchestration details

### Boundary With MPLS / IP / Custom Planner

The boundary is:

- the planner is free to choose policy semantics
- `RuntimeUpdatePlan` is the planner output contract that carries the chosen
  logical runtime result

This means:

- an MPLS planner can express labels and `LFIB`-oriented updates
- an IP planner can express route-oriented updates
- a custom planner can express another runtime realization

But the contract must stay generic enough that the platform does not become
hard-coded to one planner family.

### Boundary With Agent / Installer

The boundary is:

- `RuntimeUpdatePlan` says what node-local runtime state should exist
- runtime agent handles receipt, sequencing, validation, and reporting
- installer handles local mutation execution

The agent and installer must not need to understand:

- why a gateway was selected
- why a zone owner was selected
- why one MPLS or IP policy was chosen

They only need to apply the plan correctly and report the outcome.

## 3. NetworkMutationPlan

`NetworkMutationPlan` is the physical network mutation contract for runtime
topology and connectivity changes.

### Who Generates It

- a topology mutation compiler, coordinator, or host-side mutation planner
  generates `NetworkMutationPlan`

This generator may be driven by:

- scenario transition logic
- handover logic
- topology change diffing
- future runtime topology optimizers

It should not be generated by the routing planner purely as a side effect of
MPLS or IP policy generation.

### Who Consumes It

- host daemon consumes `NetworkMutationPlan`
- host-scoped validators and rollback handlers may also consume it

Container-local installer should not consume `NetworkMutationPlan` directly as
its main contract.

### What It Expresses

`NetworkMutationPlan` expresses physical runtime network mutation such as:

- runtime link update intent
- handover-related attach or detach
- connect or release of dynamic links
- link parameter mutation where host-scoped mutation is required
- required attachment, rebinding, or preservation semantics
- validation and rollback expectations for physical topology changes

It may refer to host-scoped execution domains such as:

- namespaces
- veth pairs
- bridges
- `docker network`
- other future virtual-link mechanisms

### What It Does Not Express

`NetworkMutationPlan` must not express:

- routing policy logic
- MPLS policy logic
- gateway-egress selection logic
- zone-egress selection logic
- orbit or visibility policy reasoning
- local forwarding state like labels, rules, or route semantics unless those
  are merely referenced as dependencies for ordering

### Boundary With Runtime Link Update / Handover / Namespace / veth / Bridge / Docker Network

The boundary is:

- runtime link update and handover intent are represented in
  `NetworkMutationPlan`
- the host daemon translates those physical mutation intents into the concrete
  mechanism of the current runtime substrate

That substrate may currently involve:

- namespaces
- veth
- bridges
- `docker network`

Later it may involve:

- more direct Linux virtual-device control
- stable-interface rebinding
- other optimized mechanisms

The contract should survive those implementation changes.

### Difference From RuntimeUpdatePlan

The difference is fundamental:

- `RuntimeUpdatePlan`
  - logical forwarding/runtime state contract
- `NetworkMutationPlan`
  - physical topology mutation contract

One answers:

- what forwarding state should nodes have?

The other answers:

- what physical connectivity mutations are required in the runtime substrate?

They may be correlated, but they are not the same contract and must not be
collapsed into one opaque structure.

## 4. Data Flow Among The Three Contracts

The intended Iteration 2 logical flow is:

- Orbit / Visibility / Zone
  - produce topology-relevant upstream results
- `TopologySnapshot`
  - captures planner-facing topology facts for one slice
- Planner
  - consumes `TopologySnapshot`
  - produces `RuntimeUpdatePlan`

Physical dynamic network update is represented in parallel through
`NetworkMutationPlan`, not by stuffing physical mutation steps into the planner
output.

### Recommended Relationship

- `TopologySnapshot`
  - source of planning truth for one slice
- `RuntimeUpdatePlan`
  - source of node-local forwarding/runtime intent
- `NetworkMutationPlan`
  - source of host-level connectivity mutation intent

### Practical Interpretation

At runtime, a scenario or slice transition may require both:

- node-local forwarding mutation
- physical topology mutation

But those should be carried through distinct contracts so that:

- planner logic stays replaceable
- runtime mutation can be optimized independently
- host-level execution does not need to parse orbit or MPLS policy

## 5. Explicitly Forbidden Couplings

The following couplings are explicitly forbidden.

### TopologySnapshot

- `TopologySnapshot` must not contain MPLS label state
- `TopologySnapshot` must not contain node-local installer command sequences
- `TopologySnapshot` must not contain Docker or Linux mutation procedures

### RuntimeUpdatePlan

- `RuntimeUpdatePlan` must not contain orbit propagation details
- `RuntimeUpdatePlan` must not contain raw visibility engine internals
- `RuntimeUpdatePlan` must not become a disguised physical network mutation
  script

### NetworkMutationPlan

- `NetworkMutationPlan` must not contain routing or MPLS policy logic
- `NetworkMutationPlan` must not encode planner reasoning about gateway or zone
  choice
- `NetworkMutationPlan` must not become the place where orbit or planner policy
  is reinterpreted

### Agent / Installer

- agent must not reverse-engineer planner logic
- agent must not reverse-engineer orbit policy
- installer must not decide gateway or zone policy
- host daemon must not silently become the hidden global planner

## 6. Still-Unsettled Questions

The following remain intentionally open after this boundary-design phase.

- final schema fields for:
  - `TopologySnapshot`
  - `RuntimeUpdatePlan`
  - `NetworkMutationPlan`
- update transport mechanism:
  - store
  - push
  - pull
  - watch
- per-container versus per-host versus hybrid agent model
- ordering of OpenSN-inspired optimization adoption
- rollback and failure-reporting contract details

## Recommended Next Step

- use this document as the boundary reference for the next schema-design phase
- define first schema drafts only after these ownership lines are accepted
- keep implementation blocked until the contracts are stable enough that code
  will not hard-wire the wrong boundary assumptions
