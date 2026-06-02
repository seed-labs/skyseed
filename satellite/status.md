# Satellite Iteration 2 Status

## Current Baseline

- The current active baseline is still a controller-driven `satellite/`
  prototype with explicit runtime state files such as `topology.json` and
  `link_state.json`.
- The codebase already contains a logical constellation inventory in
  `build_topology.py`, controller-managed runtime updates in `controller.py`,
  and a default example MPLS workflow in `mpls_control.py`.
- The present MPLS behavior is useful as a reference implementation, but it is
  not yet the permanent platform architecture.
- No Iteration 2 runtime implementation work has started yet under the new
  goal-driven architecture described in `design_principle.md`.
- Iteration 2 architecture intent is now clearer about fixed `UT` coordinates,
  stable zone membership, unique active zone serving satellites, logical G/Z
  MPLS semantics, and runtime-update architecture remaining undecided pending
  investigation.

## Iteration 2 Goal

- Establish a goal-driven, pluggable architecture for:
  - orbit-driven topology generation
  - modular routing/MPLS planning
  - agent-based runtime update
  - investigation-led runtime optimization
- Keep implementation scope inside `satellite/` unless later design review says
  otherwise.

## Completed

- Read the current `satellite/` baseline materials:
  - `README.md`
  - `AGENTS.md`
  - `docs/` design documents
  - `controller.py`
  - `mpls_control.py`
  - `build_topology.py`
- Initialized Iteration 2 collaboration documents:
  - `AGENTS.md`
  - `design_principle.md`
  - `.skills/*.md`
  - `status.md`
- Recorded that Iteration 2 implementation has not started yet.
- Incorporated clarified Iteration 2 semantics into documentation:
  - fixed `UT` coordinates for the current stage
  - stable `UT` zone membership after zone generation
  - replaceable `ZoneGenerator` direction
  - exactly one active serving satellite per zone per slice
  - G/Z as logical MPLS planning semantics, not required literal runtime tables
  - generic `RuntimeUpdatePlan` output expectations
  - runtime update architecture still open pending OpenSN investigation
- Added `docs/iteration2_architecture_understanding.md` as the current
  understanding note.

## In Progress

- Converging on stable architectural boundaries for:
  - `TopologySnapshot`
  - planner interface
  - `RuntimeUpdatePlan`
  - node-local runtime agent responsibilities
- Preparing the project for investigation-first OpenSN review before adopting
  runtime optimization ideas.
- Refining how `TopologySnapshot` should express `UT`/zone/satellite/gateway
  relationships without prematurely locking a concrete schema.

## Open Questions

- What exact input schema should the orbit pipeline accept in its first usable
  version?
- What is the minimal but sufficient `TopologySnapshot` contract for both the
  default G/Z MPLS planner and future IP planners?
- What exact `ZoneGenerator` interface should represent fixed grids,
  HEALPix-like partitioning, or user-defined zones without hard-coding one
  strategy?
- How should the system represent candidate serving satellites versus the one
  active responsible serving satellite for each zone in a time slice?
- Should the first runtime update store be file-based, controller-pushed,
  polling-based, or another mechanism before any `etcd` integration is
  considered?
- What should be the rollback and failure-reporting contract between agent and
  installer?
- What exact `RuntimeUpdatePlan` schema is sufficient for Linux MPLS/runtime
  installers without prematurely constraining future planner designs?
- How should destination-to-gateway class and destination-to-zone mapping be
  represented so that G/Z planner behavior stays replaceable?

## Risks

- The current controller-driven example flow could accidentally become the
  de facto architecture if interfaces are not defined first.
- The default MPLS planner could leak platform assumptions into unrelated
  modules.
- Zone-serving semantics could become ambiguous if uniqueness is not enforced
  explicitly in the snapshot and planner contracts.
- Premature commitment to one update store such as `etcd` could create
  unnecessary coupling.
- A runtime-update design could accidentally optimize only for the current tiny
  prototype and fail to scale toward much larger constellations.
- OpenSN ideas could be copied without enough verification if the
  investigation structure is not enforced.

## Next Recommended Action

- Review and agree on the architectural boundaries in `design_principle.md`.
- Define the first explicit schemas for `TopologySnapshot`, `ZoneGenerator`,
  and `RuntimeUpdatePlan` before starting Iteration 2 implementation work.
- Start OpenSN investigation with the structure required by
  `.skills/opensn_investigation.md`.
- Keep unresolved semantics documented as open questions instead of silently
  fixing them in code.

## Validation Record

- This status entry was prepared from repository inspection only.
- Commands used in this documentation round:
  - `sed -n '1,260p' satellite/design_principle.md`
  - `sed -n '1,260p' satellite/status.md`
  - `sed -n '1,260p' satellite/AGENTS.md`
  - `sed -n '1,260p' satellite/.skills/*.md`
- No runtime validation commands were executed.
