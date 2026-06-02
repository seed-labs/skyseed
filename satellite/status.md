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
- Iteration 2 runtime implementation has now started with a contract-skeleton
  layer inside `satellite/`, while the legacy controller-driven behavior
  remains the active execution baseline.
- The contract-skeleton layer now includes explicit validation helpers and a
  standalone mock flow, without requiring deeper runtime-path integration.
- The contract-skeleton layer now also includes a planner boundary that turns
  `TopologySnapshot` into `RuntimeUpdatePlan` through a replaceable planner
  interface and a default skeleton planner, still without changing the legacy
  runtime execution baseline.
- `RuntimeUpdatePlan` now carries a typed action model for generic node-local
  runtime intent, so planner output no longer defaults to command-shaped
  placeholder actions.
- A first end-to-end Iteration 2 control-plane demo now runs locally without
  Docker, showing a multi-slice mock orbit/topology producer feeding
  `TopologySnapshot -> Planner -> RuntimeUpdatePlan` plus transition summaries.
- A first runtime projection pipeline now runs locally without Docker, showing
  `RuntimeUpdatePlan` typed actions being projected into dry-run installer
  preview objects without executing real Linux, network, or Docker commands.
- A first complete Iteration 2 architecture pipeline now runs locally without
  Docker, showing:
  `Orbit/Topology Producer -> TopologySnapshot -> Planner -> RuntimeUpdatePlan`
  `-> Runtime Projection -> Agent/Installer Envelope`
  in dry-run form across multiple time slices.
- Based on the current implementation review, the first major Iteration 2
  control-plane architecture goal is now achieved: the control-plane pipeline
  is connected end to end as a validated dry-run architecture path, while
  runtime realization and optimization remain intentionally out of scope.
- A first runtime-realization architecture pipeline now runs locally without
  Docker, showing:
  `RuntimeUpdatePlan -> Runtime Projection -> Agent/Installer Envelope`
  `-> Distribution / Store Boundary -> Node-Local Agent Boundary`
  `-> Installer Boundary`
  in fully dry-run form across multiple time slices.
- A first backend-specific dry-run runtime realization target now runs locally
  without Docker, using a Linux-forwarding realization family only at the
  installer boundary while keeping planner output and runtime projection
  backend-neutral upstream.
- A first real end-to-end Iteration 2 satellite experiment now runs through
  the architecture pipeline, using synthetic position-driven orbit/topology
  input plus a narrow legacy runtime bridge to prove that `ut1` can ping the
  Mini Internet host `10.154.0.129` (`as154-host-new`) through
  `TopologySnapshot -> Planner -> RuntimeUpdatePlan -> runtime realization`.
- A first non-legacy Iteration 2 satellite experiment path now runs locally as
  a validated execution preview, using semi-real orbital-elements input plus a
  separate `NetworkMutationPlan` delta path and a Linux/MPLS dry-run installer
  target without relying on `controller.py` private helpers as the main
  runtime architecture.
- A first minimal real non-legacy Linux/MPLS runtime realization path now runs
  for the safest direct-gateway slice, performing real node-local
  apply/verify/rollback plus a successful `ut1 -> as154-host-new`
  (`10.154.0.129`) ping without routing through the legacy runtime bridge.
- A first complete small-scale Iteration 2 satellite Internet example now runs
  through the new architecture with semi-real multi-slice orbit/position input,
  separate `RuntimeUpdatePlan` and `NetworkMutationPlan` boundaries, real
  host-side slot rebinds, real node-local Linux/MPLS apply/verify/rollback,
  successful `ut1` and `ut2` pings to `as154-host-new`, and verified cleanup
  back to the baseline runtime state.
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
- Added `docs/iteration2_contract_boundaries.md` for Iteration 2 Phase 1:
  - `TopologySnapshot` responsibility boundary
  - `RuntimeUpdatePlan` responsibility boundary
  - `NetworkMutationPlan` responsibility boundary
  - data flow among the three contracts
  - forbidden coupling rules
- Merged contract lifecycle guidance into:
  - `docs/iteration2_contract_boundaries.md`
  - `docs/iteration2_runtime_architecture.md`
- Stopped expanding the `iteration2_*` planning/design document set unless a
  new architectural risk is discovered.
- Started goal-driven contract skeleton implementation in code:
  - added `satellite/contracts.py`
  - introduced initial `TopologySnapshot` skeleton
  - introduced initial `RuntimeUpdatePlan` skeleton
  - introduced initial `NetworkMutationPlan` skeleton
  - added explicit contract validation helpers that check both structural
    invariants and forbidden cross-boundary fields
  - added a standalone mock Iteration 2 flow in
    `satellite/mock_iteration2_contract_flow.py`
  - added legacy adapter builders that project current `topology.json`,
    `link_state.json`, UT-egress runtime payloads, and scenario slot mutations
    into the new contracts
  - kept the contract skeletons decoupled from current controller runtime paths
    for this milestone
- Completed the first Iteration 2 planner skeleton milestone:
  - added `satellite/planner.py`
  - added `satellite/planner_validation.py`
  - introduced a replaceable `Planner` abstraction
  - introduced `DefaultPlannerSkeleton` as the first default planner module
  - added planner-side input, output, and end-to-end flow validation helpers
  - updated `satellite/mock_iteration2_contract_flow.py` to exercise the real
    `TopologySnapshot -> Planner -> RuntimeUpdatePlan` path
  - kept planner output generic and placeholder-oriented instead of embedding
    orbit propagation, MPLS installation logic, or runtime-agent coupling
- Completed the RuntimeUpdatePlan action-model milestone:
  - strengthened `RuntimeUpdatePlan` so planner output uses a typed action
    abstraction instead of command-shaped placeholder actions
  - added explicit action-model versioning and reusable action helpers in
    `satellite/contracts.py`
  - added lightweight action-payload validation for service binding, traffic
    classification, forwarding entry, and node capability intents
  - updated the planner skeleton to emit typed generic actions
  - updated contract-level mock plan builders and legacy projection helpers to
    target the new action model
  - kept runtime transport, runtime-agent behavior, Docker decisions, and
    controller execution paths out of the action contract
- Completed the first end-to-end Iteration 2 control-plane demo milestone:
  - added a topology-side producer skeleton with explicit orbit, visibility,
    zone-generation, and topology-slicer boundaries
  - added a static multi-slice demo producer that emits two planner-facing
    `TopologySnapshot` instances without hard-coding a future orbit library
  - added a standalone CLI demo that runs:
    `mock topology input -> TopologySnapshot -> Planner -> RuntimeUpdatePlan`
  - added transition summaries showing how active links, zone serving, and
    per-node planner actions change across time slices
  - validated each slice for snapshot, plan, and planner-flow correctness
  - kept runtime execution, runtime-agent behavior, Docker mutation, and
    OpenSN-style runtime optimization out of scope for this milestone
- Completed the first runtime projection pipeline milestone:
  - added a replaceable runtime projection layer that consumes
    `RuntimeUpdatePlan` typed actions
  - added a dry-run installer preview contract with projection-side validation
  - added a generic dry-run projector that keeps planner output free of Linux,
    Docker, and host-specific execution details
  - added a standalone demo showing:
    `TopologySnapshot -> Planner -> RuntimeUpdatePlan -> runtime projection`
  - validated projection input and output for each time slice
  - kept runtime agents, real installers, host daemons, and physical network
    mutation out of scope for this milestone
- Completed the first usable Iteration 2 architecture pipeline milestone:
  - added `satellite/agent_envelope.py`
  - introduced a replaceable `AgentEnvelopeBuilder` abstraction
  - introduced a dry-run `AgentInstallerEnvelopeBatch` contract with
    input/output validation helpers
  - added `GenericDryRunAgentEnvelopeBuilder` to convert projected
    installer-intent previews into node-local agent/installer envelopes
  - refined runtime projection provenance so replacement and validation
    semantics remain visible downstream without leaking execution details
  - added `satellite/demo_iteration2_architecture_pipeline.py`
  - added a standalone multi-slice demo showing:
    `Orbit/Topology Producer -> TopologySnapshot -> Planner -> RuntimeUpdatePlan`
    `-> Runtime Projection -> Agent/Installer Envelope`
  - validated each slice for snapshot, plan, planner-flow, projection-input,
    projection-output, envelope-input, and envelope-output correctness
  - kept real runtime mutation, Docker orchestration, `etcd`, `eBPF`, and
    physical `NetworkMutationPlan` execution out of scope
- Completed an Iteration 2 architecture review against the current
  implementation:
  - confirmed that the current connected path is:
    `Orbit/Topology Producer -> TopologySnapshot -> Planner -> RuntimeUpdatePlan`
    `-> Runtime Projection -> Agent/Installer Envelope`
  - confirmed that "pipeline connected" currently means:
    multi-slice mock topology input is transformed into validated planner
    output, validated projection output, and validated node-local dry-run
    envelopes without bypassing any architecture boundary
  - concluded that the first major control-plane architecture goal is achieved
    at dry-run architecture level
  - recorded that store/distribution, runtime execution, installer backends,
    Linux realization, Docker/network mutation, and OpenSN-style optimization
    remain future runtime-realization work rather than missing control-plane
    wiring
- Completed the first runtime-realization architecture milestone:
  - added `satellite/distribution_store.py`
  - introduced a replaceable `EnvelopeDistributor` abstraction
  - introduced a dry-run `DistributedEnvelopeBatch` contract with
    distribution-side input/output validation
  - added `satellite/node_local_agent.py`
  - introduced a replaceable `NodeLocalAgent` abstraction and dry-run
    node-local execution report contract
  - added `satellite/installer_runtime.py`
  - introduced a replaceable `NodeLocalInstaller` abstraction and dry-run
    installer execution report contract
  - added `satellite/demo_iteration2_runtime_realization.py`
  - demonstrated the complete dry-run runtime-realization path:
    `RuntimeUpdatePlan -> Runtime Projection -> Agent/Installer Envelope`
    `-> Distribution / Store Boundary -> Node-Local Agent Boundary`
    `-> Installer Boundary`
  - validated each slice for planner-flow, projection, envelope,
    distribution-store, agent-input, agent-output, installer-input, and
    installer-output correctness
  - kept transport choice, store choice, node-agent placement, installer
    backend choice, real Linux mutation, Docker mutation, and
    `NetworkMutationPlan` execution out of scope
- Completed the first backend-specific dry-run realization validation milestone:
  - extended `satellite/installer_runtime.py` with
    `LinuxForwardingDryRunInstaller`
  - kept the installer replaceable while adding one concrete backend target:
    `linux_forwarding_dry_run`
  - projected generic installer intents into Linux-forwarding-style realization
    previews only inside the backend-specific installer adapter
  - kept runtime projection backend-neutral before the installer adapter by
    preserving generic instruction families and only advertising Linux as a
    candidate backend
  - extended `satellite/demo_iteration2_runtime_realization.py` to validate:
    - planner output remains generic
    - runtime projection remains backend-neutral before the adapter
    - backend-specific preview is isolated to the installer boundary
    - no real commands are executed
  - confirmed that the current action model was sufficient for this first
    backend-specific dry-run target, so no action-model schema refinement was
    required for this milestone
- Completed the first real end-to-end Iteration 2 satellite experiment:
  - added `satellite/experiment_topology.py`
  - added a synthetic position-driven experiment producer, visibility model,
    zone generator, and experiment topology slicer for the smallest safe
    `ut1 -> sat-a1 -> gw1` scenario
  - extended `satellite/planner.py` with
    `UtInternetExperimentPlanner`
  - kept the planner replaceable while adding one concrete G/Z-MPLS-compatible
    experiment planner that emits generic typed actions plus shared flow
    context for realization
  - added `satellite/experiment_runtime_bridge.py`
  - added a narrow legacy runtime bridge that reuses current MPLS command-spec
    execution without letting the legacy runtime recompute the planner's path
    decision
  - added `satellite/demo_iteration2_satellite_experiment.py`
  - validated the dry-run architecture path:
    `synthetic position input -> TopologySnapshot -> Planner -> RuntimeUpdatePlan`
    `-> Runtime Projection -> Agent Envelope -> Distribution`
  - validated the first real runtime path:
    `RuntimeUpdatePlan -> legacy runtime bridge -> real UT ping`
  - confirmed that `ut1` successfully pinged Mini Internet host
    `10.154.0.129` (`as154-host-new`)
  - observed that the largest real-runtime cost for this milestone is still
    host-side slot-runtime rebuild and baseline link-state replay, not planner
    contract flow
  - observed that the host must expose Linux MPLS kernel support
    (`mpls_router`, `mpls_iptunnel`, `mpls_gso`) before the runtime bridge can
    realize the experiment path
- Completed the first non-legacy Iteration 2 satellite experiment path:
  - upgraded `satellite/experiment_topology.py` so the main experiment input
    path uses semi-real orbital elements over multiple time slices instead of
    hand-written active-link state
  - kept the synthetic position-driven producer only as a fallback input
    family rather than the main experiment path
  - removed the direct `mpls_control.py` intent dependency from
    `satellite/planner.py` while preserving the replaceable
    `UtInternetExperimentPlanner` boundary
  - added `satellite/network_mutation.py` to compile
    `NetworkMutationPlan` as a host-side delta for experiment-managed slots,
    separate from node-local `RuntimeUpdatePlan` forwarding state
  - extended `satellite/installer_runtime.py` with
    `LinuxMplsDryRunInstaller` as the first non-legacy satellite-specific
    backend realization preview
  - rewrote `satellite/demo_iteration2_satellite_experiment.py` so the main
    experiment path is now:
    `semi-real orbit input -> TopologySnapshot -> Planner -> RuntimeUpdatePlan`
    `+ NetworkMutationPlan -> Projection -> Envelope -> Distribution -> Agent`
    `-> Linux/MPLS dry-run installer`
  - demonstrated two slice transitions with only slot-level delta mutation
    plans, avoiding full baseline replay for the experiment-managed scope
  - validated the full non-legacy experiment path locally without Docker or
    real runtime mutation, proving the architecture through dry-run execution
    preview rather than another legacy-backed ping run
- Completed the first minimal real non-legacy Linux/MPLS runtime realization:
  - added `satellite/linux_mpls_runtime.py`
  - introduced `MinimalHostRuntimeManager` so real execution only proceeds
    when the selected slice has a zero-op `NetworkMutationPlan`
  - introduced `MinimalRealLinuxMplsInstaller` as the first real
    backend-specific Iteration 2 runtime target
  - kept the real execution scope intentionally narrow:
    - direct-gateway slice only
    - node-local Linux/MPLS state only
    - no host-side slot/link mutation apply
  - extended `satellite/demo_iteration2_satellite_experiment.py` with
    opt-in `--execute-real`
  - kept dry-run preview mandatory before real execution
  - added real preflight capture, real apply, real verify, real ping, and
    rollback verification
  - refined the experiment planner label seeds to stay within the current
    container MPLS platform-label capacity during real execution
  - verified that the minimal real non-legacy path can:
    - apply node-local Linux/MPLS state
    - ping `10.154.0.129` from `ut1`
    - roll back the applied runtime state cleanly

## In Progress

- Converging on stable architectural boundaries for:
  - `TopologySnapshot`
  - `RuntimeUpdatePlan`
  - node-local runtime agent responsibilities
- Preparing the project for investigation-first OpenSN review before adopting
  runtime optimization ideas.
- Refining how `TopologySnapshot` should express `UT`/zone/satellite/gateway
  relationships without prematurely locking a concrete schema.
- Preparing the first planner-specific policy implementation beyond the
  current placeholder skeleton while preserving planner replaceability.
- Preparing the first planner policy that can emit richer forwarding-entry
  actions on top of the new generic action model.
- Preparing the first installer/runtime projection boundary that can consume
  typed actions without collapsing the planner contract back into shell
  commands.
- Preparing the first backend-specific realization adapter that can consume
  dry-run installer intents while still remaining separate from planner logic.
- Preparing the first dry-run distribution/store boundary that can package
  node-local agent envelopes without committing to push, pull, file, or
  `etcd` transport.
- Preparing the first backend-specific realization target that can refine
  installer previews into one concrete runtime family without committing the
  whole platform to that target.
- Preparing the first richer planner policy that can emit more concrete
  forwarding-entry intent for backend validation beyond service-binding and
  classification placeholders.
- Translating OpenSN investigation findings into Skyseed-specific interface and
  evaluation criteria without prematurely choosing `etcd`, `eBPF`, or one
  daemon shape.
- Converging on a Skyseed-specific runtime architecture that separates:
  - logical forwarding update contracts
  - physical topology mutation contracts
  - host daemon responsibilities
  - container-local installer responsibilities
- Converging on contract ownership lines before schema design:
  - topology facts
  - planner output
  - host-level mutation intent
- Returning to goal-driven implementation exploration using the current stable
  reference set instead of creating more planning documents.
- Using the new contract skeleton layer to separate:
  - topology facts from planner output
  - planner output from host-level mutation intent
  - legacy controller transport details from portable runtime plan shape

## Open Questions

- What exact input schema should the orbit pipeline accept in its first usable
  version?
- What is the minimal but sufficient `TopologySnapshot` contract for both the
  default G/Z MPLS planner and future IP planners?
- When should the current per-UT skeleton zone projection be replaced by the
  first real `ZoneGenerator` output without blocking contract progress?
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
- What should the first backend-specific installer target be:
  Linux forwarding realization, container-local execution contract, host-daemon
  bridge contract, or another dry-run target family?
- After Linux-forwarding dry-run validation, what is the next target family
  that best stress-tests abstraction boundaries:
  Linux MPLS, host-daemon realization, or another runtime backend?
- What exact `RuntimeUpdatePlan` schema is sufficient for Linux MPLS/runtime
  installers without prematurely constraining future planner designs?
- How should the future installer projection map generic action types such as
  `forwarding_entry` and `traffic_classification` into Linux-specific
  realization steps without leaking those details back into the planner
  contract?
- What minimal topology-input schema should replace the current static demo
  slice definitions first: orbit-file-backed input, a visibility snapshot
  format, or another explicit intermediate artifact?
- What should the first backend-specific dry-run projection target be:
  Linux forwarding realization, a container-local agent envelope, a host-daemon
  envelope, or another intermediate installer contract?
- What minimal response envelope should agent-side dry-run execution report
  back upstream before any real runtime installer is introduced?
- What exact `NetworkMutationPlan` schema is sufficient for host-level topology
  mutation without leaking planner policy into physical execution?
- What exact ownership relationship should exist between scenario transition
  logic and `NetworkMutationPlan` generation?
- Should snapshot bundling be only a packaging optimization, or should it
  become a first-class planning abstraction?
- When and where should logical full-state plans be compiled into delta
  execution form?
- How much target-state anchoring is required for safe delta
  `NetworkMutationPlan` execution?
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
- The first agent-envelope shape could accidentally become a fixed transport
  or delivery protocol if dry-run packaging fields are treated as runtime API
  commitments too early.
- The first distributor/agent/installer dry-run contracts could accidentally
  harden into a single runtime deployment model if backend-specific realization
  work is started without preserving abstraction boundaries.
- A Linux-forwarding-oriented adapter could accidentally become the default
  platform definition if later runtime targets are not validated against the
  same generic projection and installer boundaries.

## Next Recommended Action

- Add a separate dry-run distribution/store boundary that packages node-local
  agent envelopes for either controller-push or node-pull experiments without
  committing to a real transport backend.
- Add the first backend-specific dry-run realization target for one concrete
  runtime family such as Linux forwarding realization, while keeping planner,
  distribution, agent, and envelope contracts unchanged.
- Next, validate a second contrasting backend family such as Linux MPLS or a
  host-daemon-style realization target to prove the current abstractions are
  not accidentally Linux-forwarding-specific.
- Or add a structured agent/installer result envelope that can model success,
  partial failure, rollback intent, and observed state reporting before any
  real runtime mutation is introduced.
- Runtime optimization work could accidentally overfit the current
  `controller.py` slot model instead of producing reusable contracts.
- Schema design could accidentally start before the ownership boundaries are
  truly accepted, causing hidden coupling to be baked into Phase 1 outputs.
- Lifecycle choices could be left implicit and later force inconsistent schema
  assumptions across planner, agent, and host-daemon design.
- Documentation sprawl could delay implementation and weaken goal-driven
  progress if new planning docs continue to be added without a new risk signal.
- The first contract skeletons could accidentally harden legacy field choices
  if future implementation does not keep them explicitly replaceable.
- The first default planner skeleton could accidentally become the de facto
  policy if later MPLS and IP planners are not implemented against the same
  abstraction boundary.
- The new action types could still drift toward Linux-specific semantics if
  later planners overuse `forwarding_entry` details instead of keeping the
  contract policy-generic.
- The first control-plane demo could be mistaken for a topology model if later
  work does not keep its mock orbit and zone inputs clearly replaceable.
- The first runtime projector could accidentally become a de facto installer
  contract if later backends do not remain clearly replaceable and separately
  validated.

## Next Recommended Action

- Use the validated typed action model as the seam for the first real
  planner-policy slice, likely a minimal forwarding-entry-capable planner that
  still emits generic `RuntimeUpdatePlan` node-local intent.
- Keep runtime and OpenSN optimization work separate for now:
  the next runtime-facing step should be a projection/installer boundary, not
  Docker mutation or runtime-agent integration.
- The next runtime-facing step can now be a backend-specific dry-run adapter or
  agent envelope on top of the new projection boundary, still without real
  runtime execution.

Current implementation posture:

- Do not continue adding new `iteration2_*` planning/design docs unless a new
  architectural risk is discovered.
- Use these documents as the active implementation reference set:
  - `design_principle.md`
  - `AGENTS.md`
  - `.skills/*`
  - `docs/iteration2_runtime_architecture.md`
  - `docs/iteration2_contract_boundaries.md`
- Let Codex decide concrete fields, schema shapes, and directory structure
  during contract skeleton implementation, as long as those choices remain
  consistent with the current architectural boundaries.
- Keep unresolved semantics documented as open questions if implementation
  reveals a genuine new risk.

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
- Commands used in the contract-boundary design round:
  - `sed -n '1,320p' satellite/AGENTS.md`
  - `sed -n '1,360p' satellite/design_principle.md`
  - `sed -n '1,360p' satellite/status.md`
  - `sed -n '1,360p' satellite/docs/iteration2_architecture_understanding.md`
  - `sed -n '1,360p' satellite/docs/opensn_investigation_report.md`
  - `sed -n '1,360p' satellite/docs/iteration2_runtime_architecture.md`
  - `sed -n '1,260p' satellite/.skills/*.md`
- Commands used in the lifecycle-merge and cleanup round:
  - `sed -n '1,420p' satellite/docs/iteration2_contract_lifecycle.md`
  - `sed -n '1,420p' satellite/docs/iteration2_contract_boundaries.md`
  - `sed -n '1,420p' satellite/docs/iteration2_runtime_architecture.md`
  - `sed -n '1,420p' satellite/status.md`
- Commands used in the contract skeleton implementation round:
  - `sed -n '700,980p' satellite/controller.py`
  - `sed -n '3080,3365p' satellite/controller.py`
  - `sed -n '3480,3920p' satellite/controller.py`
  - `sed -n '4280,4865p' satellite/controller.py`
  - `sed -n '880,1180p' satellite/mpls_control.py`
- Commands used in the planner skeleton implementation round:
  - `sed -n '1,260p' satellite/AGENTS.md`
  - `sed -n '1,260p' satellite/design_principle.md`
  - `sed -n '1,260p' satellite/docs/iteration2_runtime_architecture.md`
  - `sed -n '1,260p' satellite/docs/iteration2_contract_boundaries.md`
  - `sed -n '1,260p' satellite/contracts.py`
  - `sed -n '1,260p' satellite/controller.py`
  - `sed -n '1,320p' satellite/mpls_control.py`
  - `sed -n '1,220p' satellite/.skills/planner_design.md`
  - `sed -n '1,220p' satellite/.skills/orbit_design.md`
  - `sed -n '1,220p' satellite/.skills/runtime_agent_design.md`
  - `python3 -m py_compile satellite/contracts.py satellite/planner.py satellite/planner_validation.py satellite/mock_iteration2_contract_flow.py`
  - `python3 satellite/mock_iteration2_contract_flow.py`
  - `python -m py_compile satellite/contracts.py satellite/controller.py satellite/mpls_control.py satellite/build_topology.py`
- Commands used in the RuntimeUpdatePlan action-model round:
  - `sed -n '1,260p' satellite/contracts.py`
  - `sed -n '261,520p' satellite/contracts.py`
  - `sed -n '520,860p' satellite/contracts.py`
  - `sed -n '1,320p' satellite/planner.py`
  - `sed -n '1,260p' satellite/planner_validation.py`
  - `sed -n '1,220p' satellite/.skills/planner_design.md`
  - `sed -n '1,220p' satellite/docs/iteration2_contract_boundaries.md`
  - `sed -n '1,220p' satellite/docs/iteration2_runtime_architecture.md`
  - `python3 -m py_compile satellite/contracts.py satellite/planner.py satellite/planner_validation.py satellite/mock_iteration2_contract_flow.py`
  - `python3 satellite/mock_iteration2_contract_flow.py`
  - `python3 - <<'PY' ... build_mock_contract_flow() ... PY`
  - `python3 - <<'PY' ... build_mock_runtime_update_plan(...) ... PY`
- Commands used in the first control-plane demo round:
  - `sed -n '1,260p' satellite/build_topology.py`
  - `sed -n '1,260p' satellite/contracts.py`
  - `sed -n '261,520p' satellite/contracts.py`
  - `sed -n '1,340p' satellite/planner.py`
  - `sed -n '1,260p' satellite/planner_validation.py`
  - `sed -n '1,220p' satellite/.skills/planner_design.md`
  - `sed -n '1,220p' satellite/.skills/orbit_design.md`
  - `python3 -m py_compile satellite/contracts.py satellite/planner.py satellite/planner_validation.py satellite/topology_pipeline.py satellite/demo_iteration2_control_plane.py satellite/mock_iteration2_contract_flow.py`
  - `python3 satellite/demo_iteration2_control_plane.py`
  - `python3 satellite/demo_iteration2_control_plane.py --format json`
- Commands used in the runtime projection round:
  - `sed -n '1,220p' satellite/.skills/runtime_agent_design.md`
  - `sed -n '1,260p' satellite/docs/iteration2_runtime_architecture.md`
  - `sed -n '1,260p' satellite/docs/iteration2_contract_boundaries.md`
  - `sed -n '1,360p' satellite/contracts.py`
  - `sed -n '1,360p' satellite/planner.py`
  - `sed -n '1,360p' satellite/demo_iteration2_control_plane.py`
  - `sed -n '1,360p' satellite/topology_pipeline.py`
  - `python3 -m py_compile satellite/contracts.py satellite/planner.py satellite/planner_validation.py satellite/topology_pipeline.py satellite/demo_iteration2_control_plane.py satellite/runtime_projection.py satellite/demo_iteration2_runtime_projection.py satellite/mock_iteration2_contract_flow.py`
  - `python3 satellite/demo_iteration2_runtime_projection.py`
  - `python3 satellite/demo_iteration2_runtime_projection.py --format json`
- Commands used in the contract validation helper and mock-flow round:
  - `python -m py_compile satellite/contracts.py satellite/controller.py satellite/mpls_control.py satellite/build_topology.py satellite/mock_iteration2_contract_flow.py`
  - `python satellite/mock_iteration2_contract_flow.py`
- Commands used in the small-scale Iteration 2 satellite Internet example round:
  - `python3 -m py_compile satellite/experiment_topology.py satellite/network_runtime.py satellite/linux_mpls_runtime.py satellite/demo_iteration2_satellite_experiment.py`
  - `python3 satellite/demo_iteration2_satellite_experiment.py`
  - `python3 satellite/demo_iteration2_satellite_experiment.py --format json`
  - `python3 satellite/demo_iteration2_satellite_experiment.py --execute-real`
- Boundary-correction checkpoint for the small-scale Iteration 2 example:
  - UT-side `RuntimeUpdatePlan` state is now lightweight: UT nodes carry
    default gateway-class selection, optional special-prefix override slots, and
    serving-satellite ingress-label binding only.
  - Zone owner selection is now visibility-qualified: each zone resolves from
    the intersection of visible UT candidate sets, and unresolved zones are
    represented explicitly instead of diverging silently from UT attachment.
  - `NetworkMutationPlan` remains host-side slot/link delta only; UT/satellite/
    gateway forwarding/MPLS state remains in `RuntimeUpdatePlan`.
  - Real Linux/MPLS and host-network execution still use transitional direct
    container/host control, but runtime reports now mark that posture
    explicitly as a bridge toward node-local agents and host runtime daemons.
  - Validations run for this correction round:
    - `python3 -m py_compile satellite/topology_pipeline.py satellite/experiment_topology.py satellite/planner.py satellite/planner_validation.py satellite/installer_runtime.py satellite/linux_mpls_runtime.py satellite/network_runtime.py satellite/demo_iteration2_satellite_experiment.py`
    - `python3 satellite/demo_iteration2_control_plane.py`
    - `python3 satellite/demo_iteration2_runtime_realization.py --installer linux-mpls-dry-run`
    - `python3 satellite/demo_iteration2_satellite_experiment.py`
    - `python3 satellite/demo_iteration2_satellite_experiment.py --format json`
    - `python3 satellite/demo_iteration2_satellite_experiment.py --execute-real`
