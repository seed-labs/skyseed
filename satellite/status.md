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
- Investigated OpenSN from primary sources:
  - paper
  - repository
  - README
  - selected daemon and topology-controller source files
- Added `docs/opensn_investigation_report.md` with verified findings,
  adoption guidance, and open questions.
- Added `docs/iteration2_runtime_architecture.md` to define:
  - current runtime hot paths
  - logical `RuntimeUpdatePlan`
  - physical `NetworkMutationPlan`
  - runtime role boundaries
  - minimal versus high-performance implementation directions

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
- Translating OpenSN investigation findings into Skyseed-specific interface and
  evaluation criteria without prematurely choosing `etcd`, `eBPF`, or one
  daemon shape.
- Converging on a Skyseed-specific runtime architecture that separates:
  - logical forwarding update contracts
  - physical topology mutation contracts
  - host daemon responsibilities
  - container-local installer responsibilities

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
- What exact `NetworkMutationPlan` schema is sufficient for host-level topology
  mutation without leaking planner policy into physical execution?
- How should destination-to-gateway class and destination-to-zone mapping be
  represented so that G/Z planner behavior stays replaceable?
- Should the first Skyseed runtime-update path be controller-push, node-pull,
  file or snapshot based, `etcd`-backed, or another abstraction?
- Should Skyseed adopt a per-container agent, per-host daemon, or hybrid local
  runtime model?
- Should Skyseed support optional mounted per-node config payloads in addition
  to a generic `RuntimeUpdatePlan`, or avoid that coupling at first?
- If later optimization is needed, should Skyseed first pursue direct Linux
  device control, stable-interface handover, `eBPF`, or another intermediate
  step?

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
- OpenSN’s broad daemon scope could be copied too literally and blur Skyseed’s
  desired boundary among planner, agent, installer, and platform management.
- Runtime optimization work could accidentally overfit the current
  `controller.py` slot model instead of producing reusable contracts.

## Next Recommended Action

- Review and agree on the architectural boundaries in `design_principle.md`.
- Define the first explicit schemas for `TopologySnapshot`, `ZoneGenerator`,
  and `RuntimeUpdatePlan` before starting Iteration 2 implementation work.
- Use `docs/opensn_investigation_report.md` to draft the first runtime-update
  architecture options note before any implementation work.
- Use `docs/iteration2_runtime_architecture.md` to draft the first schema note
  for `RuntimeUpdatePlan` and `NetworkMutationPlan`.
- Keep unresolved semantics documented as open questions instead of silently
  fixing them in code.

## Validation Record

- This status entry was prepared from local repository inspection plus external
  OpenSN source investigation.
- Commands used in this documentation round:
  - `sed -n '1,260p' satellite/design_principle.md`
  - `sed -n '1,260p' satellite/status.md`
  - `sed -n '1,260p' satellite/AGENTS.md`
  - `sed -n '1,260p' satellite/.skills/*.md`
- Commands and sources used in the OpenSN investigation round:
  - `git ls-remote https://github.com/OpenSN-Library/OpenSN-Library.git lwh/dev`
  - `curl -L --silent https://raw.githubusercontent.com/OpenSN-Library/OpenSN-Library/lwh/dev/daemon/main.go`
  - `curl -L --silent https://raw.githubusercontent.com/OpenSN-Library/OpenSN-Library/lwh/dev/daemon/config/config.json`
  - `curl -L --silent https://raw.githubusercontent.com/OpenSN-Library/OpenSN-Library/lwh/dev/TopoConfigurators/Standard/main.py`
  - OpenSN paper:
    `https://arxiv.org/abs/2507.03248`
  - OpenSN HTML paper view:
    `https://ar5iv.labs.arxiv.org/html/2507.03248v1`
  - OpenSN repository:
    `https://github.com/OpenSN-Library/OpenSN-Library`
- Commands used in the runtime architecture design round:
  - `sed -n '1,320p' satellite/docs/opensn_investigation_report.md`
  - `sed -n '1,320p' satellite/design_principle.md`
  - `sed -n '1,320p' satellite/status.md`
  - `rg -n "docker network|docker exec|subprocess|network connect|network disconnect|link_state|apply_scenario|attach-|detach-|connect_cross|release-slot|prepare-ut-egress-policy|install-ut-egress-mpls|runtime agent|NodeDaemon|VXLAN|ip link|tc |iptables|ip -f mpls|sysctl" satellite/controller.py satellite/mpls_control.py satellite/runtime_update_agent.sh satellite/build_topology.py`
- No runtime validation commands were executed.
