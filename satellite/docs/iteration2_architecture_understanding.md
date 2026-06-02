# Iteration 2 Architecture Understanding

This document records the current understanding of Iteration 2 architecture for
`satellite/`. Where the design is not yet settled, the uncertainty is recorded
as an open question instead of being treated as a decided fact.

## Current Skyseed Baseline

- The current baseline in `satellite/` is still controller-driven.
- `build_topology.py` produces a logical constellation inventory and current
  topology-related configuration artifacts.
- `controller.py` owns runtime orchestration in the present prototype.
- `mpls_control.py` contains the current example MPLS-oriented path planning
  and runtime programming logic.
- The current MPLS behavior is useful baseline evidence, but it is not the
  permanent platform architecture for Iteration 2.

## Orbit -> Visibility -> TopologySnapshot Pipeline

The intended Iteration 2 direction is a pipeline with separable stages:

- orbit model
  - provides time-indexed satellite state inputs
- zone generator
  - is a replaceable abstraction
  - assigns fixed `UT` coordinates into stable zones for the current stage
  - does not yet commit the project to one partitioning algorithm
- visibility model
  - computes candidate visibility relationships among satellites, gateways,
    `UT`s, and zones
- topology slicer
  - converts those results into a stable `TopologySnapshot`

`TopologySnapshot` should represent one time slice as an explicit planner-facing
artifact. Planners should not need to re-derive core facts from raw orbit data
or controller-private state.

## UT / Zone / Satellite / Gateway Relationships

Current understanding for the present stage:

- each `UT` has fixed geographic coordinates during the experiment
- each `UT` does not move during the experiment
- once zone generation assigns zones, each `UT` has stable zone membership
- `UT`s still participate in visibility and topology calculation
- zone serving-satellite selection must consider the `UT`s inside the zone, not
  only an abstract zone center
- gateways remain distinct egress objects and planner-relevant network-side
  attachment points
- satellites participate as serving nodes, transit nodes, and possible
  gateway-reachable nodes depending on the time slice

## Unique Zone Serving Satellite Semantics

For each time slice:

- each zone must have exactly one active responsible serving satellite
- this uniqueness is required by the default Z-side MPLS egress model
- backup candidates or handover windows may exist later, but the active Z-side
  egress in one slice must be unique

This means the snapshot and planner contracts should make the distinction
between:

- candidate serving satellites
- the one active responsible serving satellite

## G-Side MPLS Egress Semantics

Current understanding of the default G-side concept:

- `G` means gateway egress
- it is mainly for `UT`-to-Internet or `UT`-to-gateway-direction traffic
- the `UT` side determines the target gateway or gateway-egress class from
  destination or local policy information
- the `UT` side applies the corresponding ingress label before sending traffic
  into the satellite network
- once labeled, the MPLS core forwards the packet along precomputed
  label-switched paths toward the selected gateway

## Z-Side MPLS Egress Semantics

Current understanding of the default Z-side concept:

- `Z` means zone egress
- it is mainly for return or network-to-`UT`-direction traffic
- the network or `GW` side maps the destination `UT` or destination address to
  the `UT`'s zone
- the zone maps to exactly one active responsible serving satellite in the time
  slice
- the packet is label-switched toward that responsible satellite
- that responsible satellite delivers traffic toward the `UT` access side or
  beam abstraction

## Planner Interface Expectations

The planner boundary should stay generic and replaceable:

- input: `TopologySnapshot`
- output: `RuntimeUpdatePlan`

The default G/Z MPLS planner is only one planner module. The platform should
remain open to:

- other MPLS planner designs
- an IP-routing planner
- future hybrid planners

Intermediate satellites should not need to understand high-level G/Z concepts.
They only need the local forwarding state that results from the planner output.

## RuntimeUpdatePlan Expectations

The planner output should not be defined prematurely as two literal runtime
tables. The plan should contain the concrete runtime entries needed by the
chosen Linux MPLS or runtime installer model, potentially including:

- ingress classification rules
- label allocation results
- `LFIB` entries
- route rules
- cleanup or replacement rules
- validation expectations
- node-local subplans

The exact schema is still open.

## Agent / Runtime Installer Responsibilities

Current intended separation:

- update store
  - persists plans or plan fragments
- distributor or client
  - moves plan data to the node by push, pull, or another mechanism
- agent
  - receives or fetches node-local plans
  - validates plan envelopes and local applicability
  - invokes installation
  - reports status or failure
- installer
  - applies local Linux state only
- validator
  - checks observed local state after apply

The agent should execute local node plans only. It should not perform global
planning or remote node mutation.

The final update mechanism is still open. Push, pull, `etcd`, file or snapshot
stores, or other approaches remain candidates until the scalability and
stability tradeoffs are better understood.

## OpenSN Investigation Targets

Before choosing the long-term runtime-update architecture, OpenSN should be
investigated with special attention to:

- node-local daemon or agent model
- key-value or configuration distribution model
- avoiding excessive Docker CLI calls
- reducing unnecessary virtual link creation
- runtime topology or link update representation
- which ideas fit Skyseed
- which ideas should be rejected or postponed

Any adoption decision should be evaluated not only for the current prototype
but also for future constellations on the order of `10,000` satellites.

## Still-Open Design Questions

- What exact input schema should the first orbit pipeline accept?
- What exact `ZoneGenerator` abstraction should represent fixed grids,
  HEALPix-like partitioning, or user-defined zones?
- What is the minimal `TopologySnapshot` schema that supports both the default
  G/Z MPLS planner and future non-MPLS planners?
- How should candidate serving satellites and the one active responsible
  serving satellite be represented in the snapshot?
- What exact `RuntimeUpdatePlan` schema should be considered the first stable
  contract?
- What runtime update mechanism should be used first: push, pull, file store,
  polling, `etcd`, or another option?
- What rollback and failure-reporting contract should exist between agent and
  installer?
- How should destination-to-gateway class and destination-to-zone mapping be
  represented without overfitting to one planner?
- What scale-oriented runtime choices are acceptable for future constellations
  on the order of `10,000` satellites?
