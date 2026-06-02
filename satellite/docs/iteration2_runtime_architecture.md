# Iteration 2 Runtime Architecture

This document specializes the Iteration 2 architecture for Skyseed runtime
behavior. It is derived from the current `satellite/` baseline and the OpenSN
investigation, but it does not mandate copying OpenSN’s implementation model.

The goal is to define a runtime architecture that:

- preserves mechanism and policy separation
- avoids locking the platform to the current controller-driven prototype
- addresses runtime hot paths and dynamic network update cost explicitly
- supports a minimal first implementation path
- leaves room for a later high-performance path toward very large
  constellations

This document is architectural only. It does not implement runtime code.

## Current Skyseed Runtime Hot Paths

The current `satellite/` baseline is still centered on `controller.py`. Based
on the present code structure, the main runtime hot paths are:

- slot-runtime topology mutation
  - `attach-ut`
  - `detach-ut`
  - `attach-gw`
  - `detach-gw`
  - `connect-cross`
  - `release-slot`
  - `apply_scenario`
- runtime state persistence
  - repeated read and write of `link_state.json`
- node-local forwarding mutation
  - MPLS `LFIB` updates
  - `ip route` updates
  - `ip rule` updates
  - `iptables` NAT and filtering updates
  - `sysctl` updates
- controller-triggered agent execution
  - runtime update script injection through `docker exec`
- verification and probing
  - MPLS capability probe
  - route, label, and dataplane verification commands

These hot paths are different in nature:

- some are physical network mutation on the host or container boundary
- some are node-local Linux forwarding mutation inside containers
- some are controller-only orchestration and bookkeeping

Iteration 2 runtime architecture should treat them separately.

## Which Operations Currently Rely On Docker CLI / Docker Network

The current baseline relies on Docker-oriented control in several ways.

### Docker CLI Dependence

- `subprocess.run(...)` is used broadly in `controller.py`
- node-local runtime actions are injected via `docker exec`
- inspection and probing also rely on CLI-triggered command execution

### Docker Network / Link Mutation Dependence

- runtime slot bridge creation and deletion
- `docker network connect`
- `docker network disconnect`
- Docker network inspection during slot and endpoint handling

### Linux State Mutation Triggered Through Controller Shelling

Even when the real mutation is Linux-native, it is still currently triggered by
controller-side shelling:

- `ip -f mpls route`
- `ip route`
- `ip rule`
- `iptables`
- `sysctl`

This means the current prototype mixes:

- logical plan computation
- remote execution transport
- local mutation application
- verification

inside one controller-driven path.

## Which Operations Are Runtime-Critical

The runtime-critical operations are the ones that occur frequently or that sit
directly on scenario transition latency.

### Topology-Critical

- `UT` handover attach and detach
- `GW` handover attach and detach
- cross-plane `ISL` connect and release
- scenario convergence through `apply_scenario`

These operations can require repeated Docker network mutation and can dominate
runtime transition latency.

### Forwarding-Critical

- installing or removing Linux MPLS entries
- installing or removing route rules
- updating `iptables` state for gateway NAT and filtering
- updating per-interface `sysctl` state

These operations affect dataplane correctness immediately and are on the path
of every handover or planner-driven update.

### State-Critical

- writing `link_state.json`
- using `link_state.json` as the immediate source of truth for topology-related
  runtime decisions

This is less expensive than Docker mutation, but it is architecturally
important because current planner and controller behavior depend on it.

## OpenSN-Inspired Optimization Candidates

OpenSN suggests several useful optimization directions, but they should be
translated into Skyseed-specific architecture rather than copied literally.

### 1. Reduce Docker CLI In Runtime-Critical Paths

Candidate:

- move away from repeated controller-spawned Docker CLI subprocesses for every
  runtime action
- prefer a bounded local runtime service or a better-structured host execution
  layer

Rationale:

- process spawning and Docker CLI orchestration become expensive under frequent
  topology updates
- controller-side command fan-out does not scale cleanly toward larger systems

### 2. Separate Logical Update Planning From Physical Network Mutation

Candidate:

- define a logical contract for forwarding and topology intent
- define a separate physical mutation contract for host-level link changes

Rationale:

- current controller paths interleave the two
- the two classes of work have different optimization strategies

### 3. Reduce Destructive Link Churn

Candidate:

- avoid treating every runtime topology change as full disconnect plus
  reconnect when a lighter-weight rebinding or stable-interface mutation is
  possible

Rationale:

- frequent handovers are central to LEO experiments
- destructive link churn is one of the most expensive mutation categories

### 4. Introduce A Host-Level Runtime Service Boundary

Candidate:

- use a host daemon for physical network mutation and host-scoped state
- avoid making every physical mutation originate from the high-level planner
  path

Rationale:

- the host sees Docker, veth, bridges, and future virtual-link mechanisms
- the container-local installer should not need to understand host topology
  mutation

### 5. Keep A Future Path For Direct Linux Or eBPF-Based Optimization

Candidate:

- leave room for:
  - direct Linux virtual-device control
  - stable-interface handover mechanisms
  - future `eBPF` experiments

Rationale:

- OpenSN’s biggest runtime lesson is to reduce expensive virtual-link churn
- Skyseed should preserve that option without requiring it initially

## RuntimeUpdatePlan Logical Update Contract

`RuntimeUpdatePlan` is the logical contract emitted by a planner from one
`TopologySnapshot`.

It describes what local node state should exist for a slice, without dictating
how host-level link mutation is carried out.

### Purpose

- carry planner output from policy world into runtime world
- remain generic across:
  - default G/Z MPLS planner
  - alternative MPLS planners
  - future IP-routing planners
- isolate node-local installers from planning rationale

### Expected Contents

A `RuntimeUpdatePlan` should be able to contain at least:

- plan identifier and schema version
- target snapshot identifier and time-slice metadata
- planner identifier and planner kind
- node-local subplans keyed by node ID
- replace, supersede, or rollback semantics
- validation expectations
- optional dependency ordering hints

### Node-Local Subplan Shape

Each node-local subplan may include:

- ingress classification rules
- MPLS label allocation results relevant to that node
- `LFIB` mutations
- `ip route` mutations
- `ip rule` mutations
- `iptables` mutations
- `sysctl` mutations
- cleanup or removal actions
- post-apply validation expectations

### Important Boundary

`RuntimeUpdatePlan` must not be defined as:

- "the G-table"
- "the Z-table"
- "some exact mounted config file layout"

Those are planner semantics or installer realizations, not the portable logical
contract.

## NetworkMutationPlan Physical Network Update Contract

`NetworkMutationPlan` is a separate contract for physical or host-scoped
network mutation.

It exists because topology mutation and forwarding mutation should not be
treated as the same runtime problem.

### Purpose

- describe how the runtime fabric itself changes
- isolate host-level network mutation from planner semantics
- make topology update cost observable and optimizable

### Expected Contents

A `NetworkMutationPlan` should be able to contain:

- mutation batch identifier and schema version
- target snapshot or scenario reference
- affected slots, links, and endpoints
- operation type, such as:
  - attach endpoint
  - detach endpoint
  - connect cross-plane link
  - release cross-plane link
  - update link parameter
  - preserve interface during rebinding
- host target or mutation executor target
- ordering and atomicity hints
- rollback instructions or rollback class
- expected post-mutation topology state
- mutation validation expectations

### Why Separate This From RuntimeUpdatePlan

Because these are different questions:

- `RuntimeUpdatePlan`
  - what forwarding state should nodes install?
- `NetworkMutationPlan`
  - what host-level or topology-fabric changes are needed so those node-local
    forwarding assumptions become true?

This separation helps avoid overloading the planner with low-level Docker or
Linux link orchestration details.

## Agent vs Host Daemon vs Container-Local Installer Boundary

Iteration 2 should explicitly separate three runtime roles.

### Planner

- consumes `TopologySnapshot`
- emits `RuntimeUpdatePlan`
- may reference needed network topology mutations, but should not directly own
  Docker or Linux link orchestration

### Host Daemon

- owns host-scoped network mutation
- consumes `NetworkMutationPlan`
- is responsible for:
  - runtime slot bridge lifecycle if retained
  - endpoint connect or disconnect
  - container attachment plumbing
  - future optimized virtual-link mutation
  - host-scoped validation and rollback

The host daemon is the right place for OpenSN-inspired performance work.

### Runtime Agent

- retrieves or receives logical node-local plan material
- coordinates node-local installation
- reports success, failure, and validation results
- should not compute planner logic
- should not own broad host topology mutation

### Container-Local Installer

- executes Linux state changes inside one container or node namespace
- applies:
  - `ip route`
  - `ip rule`
  - `ip -f mpls route`
  - `iptables`
  - `sysctl`
- validates local post-apply state

### Recommended Boundary For Iteration 2

- host daemon:
  - physical network mutation
- container-local installer:
  - local forwarding mutation
- runtime agent:
  - plan intake, sequencing, validation, reporting

This is a better fit for Skyseed than a single monolithic OpenSN-like daemon.

## Minimal First Implementation Path

The first implementation path should optimize for architectural correctness and
replaceability, not maximum performance.

### Step 1

- keep the current `controller.py`-driven orchestration as a temporary outer
  coordinator
- define explicit `RuntimeUpdatePlan` and `NetworkMutationPlan` schemas in
  documents first

### Step 2

- keep topology mutation execution simple at first
- allow `controller.py` to continue triggering host-level topology mutation
  while that logic is being refactored behind a clearer boundary

### Step 3

- move node-local forwarding mutation to a clearer runtime agent plus
  installer contract
- the first transport may still be controller-pushed if needed

### Step 4

- add structured validation and failure reporting for:
  - topology mutation
  - node-local forwarding mutation

This path deliberately does not require:

- `etcd`
- host daemon rollout
- `eBPF`
- direct Linux device control refactor

from day one.

## Future High-Performance Path Toward 10k Satellites

Long term, Skyseed needs a runtime design that does not depend on
small-prototype assumptions.

### High-Performance Direction

- planner emits logical `RuntimeUpdatePlan`
- a topology mutation compiler or coordinator emits `NetworkMutationPlan`
- a host daemon executes physical network mutations without repeated
  controller-spawned Docker CLI hot-path calls
- a node-local agent or installer applies forwarding state locally
- runtime validation and telemetry become structured rather than ad hoc

### Performance Priorities

- minimize Docker CLI subprocess overhead
- minimize destructive link delete and recreate cycles
- minimize unnecessary virtual network object creation
- preserve interface stability where possible across handovers
- batch and parallelize safe host-level mutations
- keep planner output independent of the chosen transport or execution model

### Possible Future Optimization Themes

- host daemon using SDK or direct system control instead of shell fan-out
- reduced-churn rebinding semantics for handover
- direct Linux virtual-device mutation
- selective use of `eBPF` for handover-heavy paths
- better runtime state store than repeated JSON-file centric coordination

This high-performance path should remain optional until real bottlenecks are
measured.

## What Must Not Be Optimized Prematurely

- do not hard-code `etcd` as the required runtime store
- do not require `eBPF` in the first runtime architecture implementation
- do not collapse `RuntimeUpdatePlan` and `NetworkMutationPlan` into one blob
- do not force one host-daemon shape before clarifying the exact host mutation
  boundary
- do not optimize away validation and rollback semantics for the sake of speed
- do not treat current Docker slot semantics as permanent architecture
- do not optimize the current demo path in a way that blocks future planner
  replaceability

## Open Questions

- What exact schema should `RuntimeUpdatePlan` use for:
  - MPLS-specific updates
  - planner-neutral updates
  - validation and rollback
- What exact schema should `NetworkMutationPlan` use for host-level topology
  mutation?
- Should the first runtime agent be:
  - controller-pushed
  - polling
  - watch-based
  - or another model
- Should the host daemon be:
  - per host
  - per runtime namespace group
  - or something narrower
- Which current `controller.py` hot-path operations should move first behind
  the host-daemon boundary?
- Should `link_state.json` remain a temporary runtime truth source, or should
  it become a derived/debug artifact once explicit plan contracts exist?
- For handover-heavy scenarios, what is the earliest worthwhile optimization:
  - less CLI usage
  - better mutation batching
  - stable-interface mutation
  - direct Linux control
  - `eBPF`

## Recommended Next Step

- Use this document plus `opensn_investigation_report.md` to draft the first
  schema note for:
  - `RuntimeUpdatePlan`
  - `NetworkMutationPlan`
  - runtime agent response envelope
  - host-daemon mutation response envelope
- Do that before changing runtime code.
