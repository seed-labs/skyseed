# Skyseed Platform Abstraction Boundary Plan

This audit covers the current canonical route:
planned-shell `S5` full `1-shell`, `12UT/6GW` valid6 catalogs, 6-zone metadata
catalog, catalog ground placement, event-time RuntimeEpoch/ParameterEpoch,
G/Z FEC diff planning, and direct backend with `dynamic_satellite_link=0`.

Goal 1 added a regression guard for that route. Goal 2 does not change runtime
behavior. It records where the current code already follows the SEED-style
flow:

```text
logical satellite Internet description
  -> backend-neutral semantic intent / IR
  -> backend-specific realization
  -> backend-neutral and backend-specific audit
```

## Current Pipeline

The current pipeline is already split in several useful places, but the
canonical real-runtime path still carries Linux/direct-backend details through
some planner and analysis artifacts.

1. User input and scenario description

   - planned shell profile: `--orbit-input-mode planned-shell`,
     `--planned-shell-id S5`, satellite count, sample horizon, cadence
   - real catalog preview input: optional TLE/orbit data path
   - UT/GW catalog: valid6 12UT/6GW catalogs for canonical runtime
   - zone catalog: 6-zone metadata catalog for canonical runtime
   - ground placement: catalog mode and explicit gateway/UT placement
   - gateway policy: default gateway, preferred gateway lists, prefix override
     policies, competitiveness checks
   - observation intent: ping targets, path observation selectors, validation
     sampling, watcher selection
   - runtime knobs: epoch caps, ParameterEpoch drift thresholds, preflight,
     compose startup strategy, rollback/cleanup policy

2. Topology and temporal planning

   - `topology_pipeline.py` defines a clean orbit producer, visibility model,
     zone generator, and topology slicer boundary.
   - `TopologySnapshot` in `contracts.py` is the planner-facing slice contract.
   - `temporal_planning.py` enriches snapshots with runtime parameters and
     builds event-time RuntimeEpoch/ParameterEpoch queues.
   - `NetworkMutationPlan` captures host-side slot/link mutation intent for
     topology-changing epochs.

3. Planner output and runtime intent

   - `RuntimeUpdatePlan` is the main node-local runtime intent contract.
   - `DefaultPlannerSkeleton` is mostly backend-neutral.
   - `UtInternetExperimentPlanner` is the canonical experiment planner and
     emits G/Z return intent, gateway selection, path context, and shared
     label allocation metadata.
   - `gz_fec_plan()` and `diff_gz_fec_plans()` summarize G/Z FEC groups and
     changed/skipped groups between epochs.

4. Runtime realization

   - `network_runtime.py` realizes `NetworkMutationPlan` with host-side direct
     Linux netns/veth/qdisc or controller-backed slot mutation.
   - `linux_mpls_runtime.py` realizes `RuntimeUpdatePlan` through node-local
     Linux MPLS/IP/iptables/sysctl commands, currently transported with
     Docker exec batching.
   - `runtime_projection.py`, `distribution_store.py`,
     `node_local_agent.py`, and `installer_runtime.py` already contain dry-run
     boundaries for projecting logical runtime plans into installer and
     node-agent shapes.

5. Audit and analysis

   - Temporal audit checks event-time ordering, no future active link use, no
     future forwarding state, RuntimeEpoch mutation scope, ParameterEpoch
     mutation-free behavior, and rollback completion.
   - Return-path/G/Z audit checks shared G/Z backbone semantics, state scaling,
     and migration blockers from planner/runtime artifacts.
   - Timing analysis checks wall/monotonic execution marks and epoch timing
     breakdowns.
   - Regression metrics compare compact route/timing/workload gates against
     the committed baseline.

## Target Pipeline

The desired platform boundary is not "rewrite everything to BPF/qdisc". It is
to preserve the logical-to-compiled flow and make each backend attach below the
same semantic contract.

```text
User scenario and catalog inputs
  -> OrbitTopologySlice / VisibilitySlice / ZoneProjection
  -> TopologySnapshot
  -> LinkParameterTimelineIntent
  -> RuntimeEpoch / ParameterEpoch event queue
  -> RuntimeUpdatePlan + NetworkMutationPlan + GzFecIntent
  -> BackendCompilePlan
       - LinuxMplsBackend
       - DirectNetnsBackend
       - BpfLabelSwitchBackend
       - QdiscTimelineBackend
       - NodeAgentBackend
  -> BackendExecutionReport
  -> SemanticAuditReport + BackendAuditReport
  -> RegressionMetrics
```

The important shift is to keep `RuntimeUpdatePlan`, `NetworkMutationPlan`,
G/Z return intent, and ParameterEpoch timeline intent as backend-neutral as
possible, then let each backend compile them into concrete commands, maps,
qdisc changes, netns mutations, or node-agent payloads.

## Backend-Neutral Intent Candidates

These objects are already good candidates for the semantic/IR layer.

- `TopologySnapshot`
  - Mostly backend-neutral.
  - Its validators forbid MPLS labels, LFIB entries, Docker operations, route
    commands, sysctl actions, and veth/bridge operations.
  - It still carries interface names in active-link endpoints, but those names
    are logical link endpoint identifiers in the current contract.

- `RuntimeEpoch`
  - Good event-time concept for topology/forwarding-changing epochs.
  - It should remain a semantic activation unit, not a Linux apply unit.

- `ParameterEpoch`
  - Good event-time concept for parameter-only drift updates.
  - It should become a `LinkParameterTimelineIntent`: target link, activation
    time, delay/loss/bandwidth/cost values, thresholds, and validation policy.

- `NetworkMutationPlan`
  - Useful IR for host-side slot/link mutation.
  - It is not purely backend-neutral today because `execution_domain` and
    consumer names assume host network mutation, but its operations express
    semantic slot transitions before commands.

- `RuntimeUpdatePlan`
  - Main node-local semantic runtime intent.
  - Existing validators forbid direct command and Docker fields.
  - It is the right input for Linux MPLS, BPF label switching, or node-agent
    backends after a projection step.

- `G/Z return intent`
  - `return_path_intent` contains strong semantic pieces:
    gateway-zone target, zone serving satellite, local UT delivery target,
    ownership split between backbone and edge, and scale target.
  - This should be separated from current Linux compatibility metadata.

- `G/Z FEC plan` and `FEC diff`
  - Good semantic grouping layer for forward gateway-class FECs and return
    gateway-zone FECs.
  - It is close to backend-neutral, but needs naming and field cleanup before
    becoming a durable backend-neutral IR.

- `ObservationFrame`
  - Current observation data is distributed across path observation targets,
    ping validation, temporal run inspection, and runtime execution reports.
  - It should be promoted as a backend-neutral "what to observe" contract,
    with backend-specific probe adapters underneath.

- `AuditExpectation`
  - Current validation expectations exist inside `RuntimeUpdatePlan`,
    `NetworkMutationPlan`, temporal audit, return-path audit, and regression
    metrics.
  - A future `AuditExpectation` should separate semantic expectations from
    backend probe mechanisms.

## Backend-Neutral Pieces Already In Good Shape

- `topology_pipeline.py`: orbit, visibility, zone, and slicer interfaces are
  clean high-level boundaries.
- `contracts.py`: schema validators explicitly reject many command/backend
  fields at the topology, runtime plan, and network mutation levels.
- `runtime_projection.py`: dry-run projector shows the intended handoff from
  logical runtime plan to installer intent.
- `distribution_store.py`, `node_local_agent.py`, `installer_runtime.py`:
  dry-run distribution, agent, and installer boundaries are already prepared
  for future node-agent backends.
- Temporal audit concepts: event-time no-future-state checks are semantic and
  should survive any backend.
- Regression metrics: most hard gates are semantic or route-shape checks;
  backend-specific gates are explicit, such as `dynamic_satellite_link=0` and
  `ut_mpls_egress_count=0`.

## Backend Leakage

These are the main places where low-level realization details leak upward.

1. Planner emits label/MPLS compatibility details

   - `UtInternetExperimentPlanner` has planner kind `gz_mpls_experiment`.
   - It allocates `forward_labels`, `reverse_labels`,
     `shared_backbone_label_scope_id`, `policy_table_id`, and
     `policy_rule_priority`.
   - Flow contexts use `forwarding_plane:
     label_switched_internet_egress` and outcomes such as
     `transit_label_swap`.
   - Gateway selection emits `ingress_transport.encapsulation:
     mpls_label_impose`.

2. G/Z return intent mixes semantic target and Linux realization metadata

   - Strong semantic fields exist:
     `shared_backbone_return_target`, `candidate_shared_zone_return_route`,
     `edge_local_delivery_target`, and `state_ownership`.
   - The same payload also includes
     `candidate_shared_return_label_stack`,
     `current_compatibility_reverse_labels`,
     `current_compatibility_label_scope_id`, and
     `current_linux_realization`.

3. G/Z FEC diff naming assumes LFIB

   - `gz_fec_plan()` groups gateway-class and gateway-zone FECs in a mostly
     semantic way.
   - `diff_gz_fec_plans()` reports
     `estimated_lfib_route_operation_items`, which is a Linux/MPLS estimate,
     not a backend-neutral operation estimate.

4. ParameterEpoch realization is coupled to qdisc/netem in the direct backend

   - `network_mutation.py` detects parameter-only changes in a semantic way
     from `delay_ms`, `loss_percent`, `bandwidth_mbps`, and `routing_cost`.
   - `network_runtime.py` compiles those values directly into `tc qdisc netem`
     arguments in the direct netns/veth/qdisc installer.
   - This is a healthy backend adapter pattern, but the plan does not yet name
     the intermediate object as a `LinkParameterTimelineIntent`.

5. Host mutation IR names direct Linux concepts

   - `NetworkMutationPlan.execution_boundary.consumer` is
     `host_network_runtime_manager`.
   - Operations use `execution_domain: host_network_mutation`.
   - Runtime installers are named around netns/veth/qdisc and controller-backed
     slot mutation.

6. Runtime reports and analysis assume Linux/Docker hot paths

   - `linux_mpls_runtime.py` emits `runtime_boundary:
     node_local_linux_mpls_only` and `execution_transport:
     transitional_node_local_batch_via_docker_exec`.
   - Timing reports expose host MPLS preparation, qdisc wall time, Docker exec
     batching, and node-local batch plans.
   - These are valuable backend-specific measurements, but should be under a
     backend report namespace when multiple backends exist.

7. Audits inspect Linux/MPLS artifacts in some places

   - Return-path audit is mostly semantic, but it still counts reverse labels,
     candidate label stacks, LFIB-style blocker IDs, and MPLS egress state.
   - Timing/regression metrics include backend-specific gates such as
     `ut_mpls_egress_count` and `dynamic_satellite_link`.

8. Topology construction still prepares MPLS capability

   - `starlink_shell_topology.py` and `seed_integration.py` enable MPLS
     readiness during topology construction.
   - This is acceptable for the canonical Linux route, but it is lower-layer
     setup, not satellite logical intent.

## G/Z FEC Planner Judgment

The current G/Z FEC planner is not a Linux install plan, but it is not fully
backend-neutral either.

It is backend-neutral in these ways:

- groups forward state by selected gateway class
- groups return state by selected gateway, zone, and responsible satellite
- exposes flow IDs, UT IDs, affected semantic nodes, zones, and shared return
  state shape
- produces changed/skipped FEC groups independent of command execution

It leaks Linux/MPLS in these ways:

- FEC signatures include concrete `forward_labels` and `reverse_labels`
- return FECs include reverse label paths
- operation estimates are named as LFIB route operation items
- upstream flow contexts already contain MPLS label allocation

Near-term conclusion: keep the current G/Z FEC artifacts stable for the
canonical route, but introduce a future `GzFecIntent` view that names generic
transport tokens or path-segment identifiers separately from backend-compiled
labels.

## ParameterEpoch Judgment

ParameterEpoch can and should be abstracted as `LinkParameterTimelineIntent`.

What is already semantic:

- `DistanceElevationRuntimeParameterModel` derives delay, loss, bandwidth, and
  routing cost from topology/visibility facts.
- `network_mutation.py` compares source and target runtime parameters and
  emits `update_slot_parameters` when only link parameters drift.
- The real-runtime queue distinguishes ParameterEpoch from RuntimeEpoch and
  prevents topology/forwarding mutation in ParameterEpoch paths.

Where backend binding appears:

- Direct backend applies parameter updates with `tc qdisc netem`.
- Timing fields expose `parameter_qdisc_apply_seconds` and
  `qdisc_apply_wall_seconds`.
- Backend reports currently surface qdisc names at the same level as semantic
  ParameterEpoch status.

Near-term conclusion: do not change the canonical behavior. First extract a
documented `LinkParameterTimelineIntent` schema/view from existing
`runtime_parameters` and `update_slot_parameters` operations. Then let
`QdiscTimelineBackend` compile it to `tc netem`, while other backends compile
the same intent to BPF maps, node-agent instructions, or simulated link-state
updates.

## Future Backend Attachment Points

- `LinuxMplsBackend`
  - Attach below `RuntimeUpdatePlan` / `GzFecIntent`.
  - Compile forwarding entries, G/Z return intent, and label-switching
    transport into Linux MPLS/IP/iptables/sysctl commands.
  - Own backend-specific probes for `ip -f mpls route`, `ip route`, `ip rule`,
    `iptables`, and MPLS sysctls.

- `DirectNetnsBackend`
  - Attach below `NetworkMutationPlan`.
  - Compile slot attachment/rebind/deactivate operations into netns/veth/bridge
    actions.
  - Continue to own `dynamic_satellite_link=0` and direct host artifact cleanup
    gates for the canonical route.

- `BpfLabelSwitchBackend`
  - Attach below `RuntimeUpdatePlan` / future `GzFecIntent`.
  - Consume semantic path/FEC groups and compile them into BPF map updates.
  - Should not require planner-generated Linux label numbers; it should either
    allocate backend tokens itself or consume backend-neutral transport IDs.

- `QdiscTimelineBackend`
  - Attach below future `LinkParameterTimelineIntent`.
  - Compile delay/loss/bandwidth/cost timeline updates into `tc qdisc netem`
    or equivalent qdisc operations.
  - Keep qdisc timing and cleanup under backend-specific reports.

- `NodeAgentBackend`
  - Attach below the existing distribution and node-local agent boundaries.
  - Consume per-node installer envelopes derived from `RuntimeUpdatePlan`,
    `GzFecIntent`, and `LinkParameterTimelineIntent`.
  - May wrap Linux MPLS, BPF, qdisc, or future in-node runtime services without
    changing planner semantics.

## Audit Platformization

Semantic audits should inspect intent and reported semantic outcomes.
Backend-specific audits should inspect concrete realization state.

Backend-neutral or mostly backend-neutral today:

- temporal audit: no future active link, no future forwarding state,
  RuntimeEpoch/ParameterEpoch distinction, rollback completion as a lifecycle
  event
- G/Z semantic audit: current vs target state scaling, shared G/Z backbone
  readiness, gateway-zone target grouping, UT-edge delivery split
- regression workload counts: epoch counts, replenishment count, changed/skipped
  G/Z FEC groups, affected semantic nodes
- timing mark integrity: event-time vs wall/monotonic timing separation

Needs backend-specific adapters:

- Linux MPLS route/LFIB probes
- `ut_mpls_egress_count`
- qdisc/netem apply and cleanup validation
- Docker exec and node-local batch execution reports
- direct netns/veth/bridge cleanup and residue checks
- BPF map state validation, when that backend exists
- node-agent delivery/ack/probe health, when that backend exists

Suggested audit split:

```text
AuditExpectation
  semantic:
    - G/Z target grouping and scale invariants
    - RuntimeEpoch / ParameterEpoch event-time invariants
    - link parameter timeline intent fulfilled
    - observation target reachable according to semantic path intent
  backend:
    - Linux MPLS probes
    - direct netns/veth cleanup
    - qdisc/netem state
    - BPF map entries
    - node-agent execution reports
```

Regression metrics should keep the canonical route gates, but future baselines
should label backend-specific gates under a backend namespace instead of
pretending they are universal semantic gates.

## Minimal Refactoring Plan

These steps are intentionally small and should be done after the current
canonical baseline guard remains green.

1. Add read-only schema/view helpers, not behavior changes

   - `GzFecIntent` view over current `gz_fec_plan()` output.
   - `LinkParameterTimelineIntent` view over current ParameterEpoch
     `update_slot_parameters` operations.
   - `ObservationFrame` view over current ping/path observation selectors.
   - `AuditExpectation` view over existing validation expectation fields.

2. Rename or duplicate backend-leaking report fields with neutral aliases

   - Add `estimated_backend_operation_items` next to
     `estimated_lfib_route_operation_items`.
   - Add `transport_token_stack` or `path_segment_tokens` next to
     `reverse_labels` in new views.
   - Keep old names until the canonical baseline and downstream reports are
     migrated.

3. Move Linux/MPLS realization metadata out of semantic return intent

   - Keep `shared_backbone_return_target`,
     `candidate_shared_zone_return_route`, and `edge_local_delivery_target`
     in semantic intent.
   - Move `candidate_shared_return_label_stack`,
     `current_compatibility_reverse_labels`, and `current_linux_realization`
     into a Linux MPLS backend-compatibility section or projection report.

4. Introduce backend compile report names

   - `BackendCompilePlan` for concrete command/map/qdisc/node-agent payloads.
   - `BackendExecutionReport` for apply, rollback, cleanup, probe, and timing.
   - Keep existing canonical runtime fields stable while adding the new names.

5. Split audit adapters

   - Keep temporal and G/Z semantic checks independent of Linux command output.
   - Put Linux MPLS, qdisc, direct netns, and future BPF probes behind
     backend-specific audit adapters.

## Do Not Touch Yet

Avoid these changes until the baseline guard and current run evidence are
preserved across smaller steps.

- Do not change G/Z return semantics or grouping rules.
- Do not change RuntimeEpoch/ParameterEpoch scheduling or event-time behavior.
- Do not remove current `forward_labels` / `reverse_labels` from canonical
  artifacts.
- Do not rewrite `linux_mpls_runtime.py` into a generic backend interface in
  one step.
- Do not replace direct netns/veth/qdisc behavior while the canonical route is
  the only stable multi-hour evidence.
- Do not introduce a broad BPF or qdisc backend implementation in the
  abstraction audit phase. Experimental BPF work must stay opt-in, narrow, and
  rollback-clean until it has comparable evidence.
- Do not change regression baseline gates without a deliberate baseline
  migration.

## Goal 16 BPF Dataplane Canary Boundary

`--bpf-dataplane-canary` is the first live dataplane canary, not a backend
replacement. It is default-off and only attaches after a bounded real-runtime
run has a selected successful UT ping. The canary scope is one UT access
host-side ingress path and one target IP:

- BPF owns `active_generation`, generation status, a single canary
  `fec_forwarding` redirect/drop decision, and packet counters for that target.
- BPF owns ParameterEpoch active/loss/drop/mark/counter decisions for that
  canary target only.
- Delay, bandwidth, and rate remain on the existing qdisc/netem path.
- Non-canary packets fall through to the canonical Linux/direct backend.
- Rollback detaches tc-BPF, unpins/deletes maps, and lets the normal post-run
  preflight prove the workspace is clean.

The qdisc timeline is still blocked or trace-only. This canary does not load
TheaterQ, a custom kernel module, or a qdisc timeline dataplane.

## Short Conclusion

Skyseed already has the right architectural direction in its contract modules:
`TopologySnapshot`, `RuntimeUpdatePlan`, `NetworkMutationPlan`, runtime
projection, distribution, node-agent, and installer dry-run boundaries all
support the SEED-style logical description to compiled backend model.

The biggest coupling is in the canonical experiment planner and analysis
artifacts, where label/MPLS compatibility details are still embedded in flow
contexts, G/Z return intent, FEC diffs, and audit summaries.

The first useful refactor is not a backend rewrite. It is to add stable views
for `GzFecIntent`, `LinkParameterTimelineIntent`, `ObservationFrame`, and
`AuditExpectation`, then compile those views into the existing Linux/direct
runtime path. BPF, qdisc, and node-agent optimizations can then be explored in
parallel under backend compile/execution adapters without disturbing the
canonical Linux/direct backend.
