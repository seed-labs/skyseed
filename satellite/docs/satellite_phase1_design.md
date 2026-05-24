# Satellite Phase 2 Slot-Based Dynamic Link Design

Chinese version:
[satellite_phase1_design.zh.md](/home/seed/skyseed/satellite/docs/satellite_phase1_design.zh.md:1)

This document describes the current mainline design for the satellite example.
The old candidate-link / snapshot-activation mainline is retired for this
prototype stage.

## Scope

- keep the prototype inside `satellite/`
- no SEED core changes by default
- keep the scale fixed at 3 `UT`, 6 `ST`, 3 `GW`
- restore the full `B00 miniInternet` as the ground-side Internet structure
- validate slot-based runtime link management
- validate minimal controller-managed `UT egress MPLS` for currently attached
  supported `UT`s, with selected-`GW` `NAT/general return path`
- do not require real `MPLS`, large scale, or a custom frontend

This phase explicitly does not do the following:

- it does not restore the old `forwarding_state`, `candidate`, or `snapshot` workflows
- it does not introduce real distributed `MPLS` control-plane protocols such as `LDP`, `RSVP`, or `SR`
- it does not run `OSPF/iBGP/eBGP` on the satellite runtime side
- it does not provide active session continuity across scenario/gateway changes
- it does not provide concurrent runtime transactions or multi-writer safety

## Runtime Boundary

- SEED creates base containers, fixed intra-plane links, and the compile-time `miniInternet`
- `controller.py` owns runtime slot bridge creation and runtime connect/disconnect
- all runtime state is explicit in `link_state.json`
- no hidden background controller logic
- `link_state.json` remains limited to satellite slot runtime plus `fixed_isl`
- `miniInternet` internal links are not mirrored into `link_state.json`

## UT Address Semantics

The current prototype distinguishes two different address roles:

- `UT stable identity address/prefix`
  - `ut1 = 100.64.0.1/32`
  - `ut2 = 100.64.0.2/32`
  - `ut3 = 100.64.0.3/32`
  - these addresses live on `dummy0` and remain unchanged across `UT-ST` attach/detach
  - future forwarding/MPLS work should treat them as service endpoints, `FEC` targets, and user identity
- `UT-ST runtime slot link address`
  - for example `10.90.x.x/29`
  - these addresses describe only the current access-link next hop
  - slot-link IPs must not be treated as user identity addresses

## Routing Boundary

- the restored `miniInternet` keeps SEED-managed `Routing + Ebgp + Ibgp + Ospf`
- `GW` ground-side IX uplinks keep SEED-managed `eBGP`
- `AS250` and the satellite-runtime side of `AS65000` are masked from SEED `Ospf/Ibgp`
- `UT-ST`, `GW-ST`, cross-plane `ST-ST`, and the current fixed intra-plane `ISL` remain controller-owned runtime topology semantics

## Link Model

### `UT-ST`

- each `UT` owns 2 access slots
- each slot is one Docker bridge network
- initialization connects only the `UT` side
- runtime attach connects or disconnects one `ST`
- if no free slot exists, the controller must fail clearly

### `GW-ST`

- each `GW` owns 2 gateway slots
- each slot is one Docker bridge network
- initialization connects only the `GW` side
- runtime attach connects or disconnects one `ST`
- if no free slot exists, the controller must fail clearly

### Fixed Intra-Plane `ISL`

- `sat-a1 <-> sat-a2 <-> sat-a3`
- `sat-b1 <-> sat-b2 <-> sat-b3`
- these links remain compile-time fixed p2p links
- they appear in `link_state.json` as `fixed_isl`

### Cross-Plane `ISL`

- orbit-a and orbit-b share 3 reusable cross-plane slots
- each slot is one shared Docker bridge network
- initialization creates the bridge only
- runtime connect attaches two `ST` endpoints to the same bridge
- disconnect removes both endpoints and returns the slot to free state

## State Model

`link_state.json` is the controller-owned runtime state file.

Each slot entry records:

- `slot_name`
- `slot_type`
- `network_name`
- `docker_network_name`
- `subnet`
- `bridge_gateway_ip`
- `preconnected_endpoints`
- `dynamic_endpoints`
- `occupied`
- `binding`

For cross-plane slots, `binding` records:

- `satellite_a_id`
- `satellite_b_id`
- `satellite_a_direction`
- `satellite_b_direction`

For fixed intra-plane links, `slot_type=fixed_isl` and `occupied=true`.

## Safety Rules

- after each `docker network connect`, the target container must gain exactly 1 new interface
- if the delta is not exactly 1, abort immediately
- only rename the discovered new interface
- do not rename any uncertain existing interface
- do not write `link_state.json` on failure
- if cross-plane second-end connect fails, roll back the first end

## Current CLI Surface

Initialize all runtime slot bridges and fixed-side preconnects:

```bash
python3 satellite/controller.py --init-slot-runtime
```

Show runtime state:

```bash
python3 satellite/controller.py --show-link-state
```

Run the read-only `MPLS-0` capability probe:

```bash
python3 satellite/controller.py --probe-mpls
python3 satellite/controller.py --probe-mpls --probe-report satellite/configs/mpls_probe.json
```

The probe checks:

- host-side `modprobe` readiness for `mpls_router` and `mpls_iptunnel`
- host-side visibility and non-zero value of `net.mpls.platform_labels`
- host-side `iproute2` support for `ip -f mpls route` and `encap mpls`
- currently running `ST/GW` containers for container-visible, non-zero `MPLS` sysctls and Docker capability posture

Run the current controller-driven `MPLS-2` static LSP workflow:

```bash
python3 satellite/controller.py --install-static-mpls-lsp sat-a1:sat-b1:gw1
python3 satellite/controller.py --verify-static-mpls-lsp sat-a1:sat-b1:gw1
python3 satellite/controller.py --cleanup-static-mpls-lsp sat-a1:sat-b1:gw1
```

The existing regression wrapper remains available:

```bash
python3 satellite/mpls_static_lab.py
python3 satellite/mpls_static_lab.py --cleanup
```

Attach and detach `UT`:

```bash
python3 satellite/controller.py --attach-ut ut1:sat-a1
python3 satellite/controller.py --attach-ut ut1:sat-a1 --slot-name ut1-access-slot1
python3 satellite/controller.py --detach-ut ut1
```

Attach and detach `GW`:

```bash
python3 satellite/controller.py --attach-gw gw1:sat-b1
python3 satellite/controller.py --attach-gw gw1:sat-b1 --slot-name gw1-gateway-slot1
python3 satellite/controller.py --detach-gw gw1
```

Connect and release cross-plane `ISL`:

```bash
python3 satellite/controller.py --connect-cross sat-a1:sat-b1:left:right
python3 satellite/controller.py --connect-cross sat-a1:sat-b1:left:right --slot-name ab-cross-slot2
python3 satellite/controller.py --release-slot ab-cross-slot0
```

If `--slot-name` is not provided for attach/connect operations, the controller
uses the first free slot in sorted slot-name order.

## Bootstrap Interface Caveat

- some containers may still keep SEED bootstrap-era `eth*` interfaces in `DOWN` state
- this is not treated as a slot-runtime bug by itself
- the controller validates slot safety by comparing interface sets immediately before and after `docker network connect`
- the success condition is “exactly one newly appeared interface”, not “no old bootstrap `eth*` remain”

## Validation Focus

The current prototype is considered successful if it demonstrates:

- bounded slot resources
- runtime bridge reuse
- controller-visible state consistency
- safe rollback on partial failure
- easy inspection from Docker and `link_state.json`

For the next stage entrance, `MPLS-0` adds one more validation question:

- can the current host and Docker runtime support the planned centralized Linux kernel `MPLS` dataplane without changing the slot-runtime state model
- “support” here now specifically requires `net.mpls.platform_labels > 0` on both the host and each MPLS-participating runtime container, not just kernel-module visibility

`MPLS-2` adds one more boundary clarification:

- explicit static `LSP` intent and static `LFIB/IP` route install may now be triggered from `controller`
- `controller` now validates that the required path is already present in `link_state.json` before installing Linux `MPLS` routes
- this is still not topology-driven routing, dynamic path computation, or handover-aware `MPLS` orchestration

`MPLS-3` moves one step closer to the intended satellite use case while still
remaining at the example/runtime layer:

- `controller` can now resolve one explicit `UT` Internet-egress intent, and it
  also exposes serial all-attached wrappers for every currently attached
  supported `UT`
- it derives the current serving satellite from `link_state.json`
- it reads occupied `gw_access` slots and selects the shortest-hop path across the active satellite graph
- it converts that path into an explicit `UT egress MPLS intent/profile` and
  programs Linux `MPLS/IP` routes as a `default Internet egress` dataplane:
  the `UT` receives a default route toward its current serving satellite, while
  the serving satellite classifies `UT`-sourced non-local traffic into a
  dedicated source-policy table with controller-derived local/internal
  exclusions, plus a minimal selected-`GW` `NAT/general return path`

The current `MPLS-3` dataplane boundary is now clearer:

- `UT stable identity` still lives on `dummy0`
- that stable identity is still the controller/FEC identity, not the current Linux business-source address
- the active Linux dataplane realization for `UT egress` is the occupied UT access slot, interface, and access-link IP
- `UT egress` core forwarding semantics no longer depend on an enumerated
  external host-prefix allowlist; the controller now derives local/internal
  exclusions from `topology.json` and treats the remaining `UT`-sourced traffic
  as default Internet egress toward the currently selected gateway
- with host `bridge netfilter` passthrough prepared (`net.bridge.bridge-nf-call-{iptables,ip6tables,arptables}=0`), the controller-managed `ut1 -> 10.154.0.129` path now runs end-to-end over Linux kernel `MPLS`
- that host bridge-netfilter preparation is still example/runtime logic and should not be treated as a `SEED core` requirement yet
- the current regression entrypoint is `satellite/mpls_ut_egress_smoke.py`, which keeps the path fixed at `ut1 -> sat-a1 -> sat-b1 -> gw1 -> 10.154.0.129`, validates layered dataplane evidence, then cleans forwarding state without releasing runtime slots
- the current return path is intentionally minimal: the selected gateway
  installs Linux `iptables` `SNAT/FORWARD` rules toward its bootstrap-side
  miniInternet IP (`10.65.21.2`, `10.65.22.2`, or `10.65.23.2`) and relies on
  conntrack/NAT reverse mapping for replies from arbitrary miniInternet hosts
- the current all-UT scope is still intentionally small:
  default `t0/t1` scenarios now attach `ut1`, `ut2`, and `ut3`; the verified
  all-UT path still remains limited to those serial example/runtime scenario
  transitions
- gateway/scenario changes may still break in-flight sessions; active session
  continuity is intentionally out of scope for this step
- controller state mutation remains serial-only for now; do not parallelize attach/release/connect actions against the shared `link_state.json`

`MPLS-4` first-stage work keeps the same boundaries but adds explicit scenario
reconciliation:

- `controller.py --apply-scenario t0|t1` reconciles one named runtime scenario
  at a time by releasing mismatched managed slots and then re-attaching the
  required `UT`, `GW`, and cross-plane bindings serially
- `controller.py --verify-scenario t0|t1` validates that the resulting
  `link_state.json` matches the selected scenario and that `ut1` now resolves
  to the expected serving satellite, selected gateway, and shortest satellite
  path
- `satellite/mpls_scenario_smoke.py` runs
  `cleanup forwarding -> apply scenario -> verify scenario -> install ut1 egress MPLS -> verify dataplane -> cleanup forwarding`
  and proves that topology changes can be applied in-place without restarting
  containers
- `satellite/mpls_scenario_smoke.py --scenario t0|t1 --all-ut` now
  applies the same serial scenario flow to every currently attached supported
  `UT` and additionally exercises an arbitrary miniInternet host target
  (`10.160.0.71`)
- real runtime validation now also covers all three default `UT`s
  (`ut1`, `ut2`, `ut3`) over the current `default Internet egress` dataplane
  in actual containers, including one strict
  `cleanup all -> apply scenario -> reinstall all -> verify all` pass
- single-`UT` cleanup must now preserve shared `MPLS input` interfaces that
  are still required by other installed `UT` egress plans; the example/runtime
  controller now treats that as part of forwarding cleanup correctness
- current measured serial runtime timings are already large enough to motivate
  later parallel runtime/controller update work:
  `apply_scenario t1 = 44.78s`, and one strict all-UT
  `t1 -> cleanup all -> t0 -> install all -> verify all` pass measured
  `cleanup_all=11.35s`, `apply_scenario t0=48.51s`,
  `install_all=39.98s`, and `verify_all=16.17s`
- cleanup still clears forwarding state only; it does not release the selected
  runtime topology
- this is an explicit step toward the final continuous `t0 -> t1 -> ...`
  scenario loop, but it still does not provide session continuity or
  concurrent mutation support

`MPLS-4` second-stage work adds the first explicit loop runner while keeping the
same example/runtime scope:

- `satellite/mpls_scenario_loop.py` repeats the same serial scenario
  cycle for `t0`, `t1`, `t0`, `t1`, ... without restarting containers
- running `python3 satellite/mpls_scenario_loop.py` now starts that
  demo loop directly with an infinite run and a 5-second interval by default;
  `--max-rounds` can be used for bounded soak runs
- every round still performs
  `cleanup forwarding -> apply scenario -> verify scenario -> install ut1 egress MPLS -> verify dataplane -> cleanup forwarding`
- the loop runner prints the current scenario, selected gateway, selected path,
  and forward/reverse label sequence after each successful round
- `Ctrl-C` now triggers best-effort forwarding cleanup plus a final
  `link_state` dump before the loop exits
- each successful round also runs a first residue/stability observation pass:
  forwarding cleanup remains empty, scenario/link-state consistency still holds,
  managed slot bridge occupancy still matches `link_state`, and no extra
  satellite runtime interfaces remain attached
- if any serial runtime mutation or MPLS install/verify step fails, the loop
  runner aborts the current round, performs best-effort forwarding cleanup,
  prints the current `link_state`, and exits instead of continuing with an
  uncertain mixed state
- current bounded soak evidence is now available:
  a 10-round run completed successfully, and a 50-round target was observed
  through 44 consecutive successful rounds before an operator stop, with no
  stale `LFIB`, stale IP route, `link_state` mismatch, or stale satellite
  runtime interface detected in the completed rounds
- `mpls_scenario_loop.py --all-ut` exists for explicit all-UT demo runs, but
  the default continuous loop and the longest repeated-transition soak evidence
  still center on the default ut1-only path; use the all-UT smoke wrapper
  first when validating all-UT changes
- if `Ctrl-C` lands in the middle of an in-flight runtime mutation, the
  forwarding cleanup remains best-effort only and the topology may remain in a
  partial transition state; rerun the target scenario or smoke wrapper once to
  reconcile topology explicitly before continuing
- this is the current regression/stability entrypoint for repeated transition
  validation, but it still does not provide background topology watching or a
  generic long-term scenario API

## Controller / Node Boundary

The long-term architecture should keep this boundary explicit:

- the `controller` remains responsible for globally informed work:
  topology snapshot, attachment state, path computation, gateway selection,
  forwarding/label/route plan generation, and scenario-level consistency
  decisions
- the `controller` should not remain a permanent "outside god hand" that edits
  each container's internal state command-by-command
- each satellite/gateway/UT node should eventually receive only its own local
  update plan, and a local in-container agent should apply route, `iptables`,
  `LFIB`, and interface mutation

So the intended scalability path is:

`controller global decision -> per-node update plan -> node local agent -> in-container execution`

not:

`controller global decision -> controller directly docker-exec into every node forever`

Operationally, this means:

- global decision-making stays centralized
- local forwarding mutation moves into the node/container
- nodes should not need global mini-Internet internals
- the controller may know topology, attachment state, its own generated
  forwarding plan, and necessary health/ack/error signals
- the controller should not rely on unrealistic omniscient dataplane reads from
  arbitrary containers to repair forwarding

The current cleanup/observability/batching step now starts expressing `UT
egress` execution as a controller-side per-node bundle. It is still executed
serially by the controller today, but it should be treated as a future
local-agent contract rather than a permanent shell-orchestration model.

The payload and interface naming should also leave room for the next control
plane hop:

- current transport remains `controller_serial_docker_exec`
- future transport should be modeled as
  `in_emulation_control_plane_delivery`
- future distribution source may be a `ground_control_host` or `data_center`
  inside the emulation
- distribution target should remain a node-local endpoint only; each
  `node_plan` should contain only node-local forwarding/update information
  rather than full topology or full mini-Internet routing state
- this same payload boundary should remain compatible with future
  `SatelliteNode` / `GatewayNode` wrappers or metadata abstractions inside
  skyseed, even before any SEED core changes exist

## Distributed Runtime Update Protocol

The current controller-side batch payload should now be treated as an early
distributed runtime update protocol envelope, even though transport is still
host-local today.

At the payload level, the protocol should carry:

- `plan_id`, `generation`, `sequence`, and `created_at` so retries and later
  in-emulation delivery can refer to a stable update instance
- `replayable` and `idempotency_scope` so replay/retry semantics are explicit
  instead of being hidden in shell behavior
- transport-neutral routing of responsibility:
  `controller_process_on_host -> controller_serial_docker_exec` today,
  `ground_control_host/data_center -> in_emulation_control_plane_delivery ->
  node_local_forwarding_agent` later

At the `node_plan` level, the contract should carry:

- `node_plan_id` as the node-local unit of application and retry
- `apply_mode` as an ordered command list, not an implicit controller loop
- `replay_policy` and `expected_ack_mode`
- per-command logical metadata such as `logical_kind`, `command_family`, and
  `idempotency_key`
- an agent-facing result contract that can eventually be returned by a local
  agent even though the current transport is still controller-side

The current implementation does not yet provide a persistent replay store or
distributed ack path, but the schema should already make those future pieces
additive rather than disruptive.

## ACK / Error Contract

The response envelope should now be treated as a future agent-facing
ACK/error/apply-result contract, not just a controller-side debug structure.

It should carry at least:

- envelope identity:
  `protocol_version`, `agent_schema_version`, `supported_protocol_versions`,
  `node_plan_id`, `plan_id`, `generation`, `sequence`
- `ack`:
  `ack_status`, `expected_ack_mode`
- `apply_result`:
  `apply_status`, `applied_commands`, `skipped_commands`, `failed_command`,
  `stdout_summary`, `stderr_summary`
- `error`:
  `error_code`, `error_category`, `error_message`, `error_details`,
  `failed_command_id`, `failed_command_family`, `failed_logical_kind`,
  `retryable`, `fatal`, `suggested_action`
- `timestamps`:
  `execution_started_at`, `execution_finished_at`
- `replay_info`:
  `replay_detected`, `already_applied`, `replay_policy`,
  `idempotency_scope`
- `validation_report`:
  `validation_status`, `validation_errors`, `unsupported_commands`,
  `unsupported_command_families`, `unsupported_protocol_version`,
  `malformed_payload`, `missing_required_fields`, plus reserved response paths
  for validation/capability/protocol rejection
- `execution_nonce`
- `agent_health`

The current schema reserves at least these error codes:

- `none`
- `transport_error`
- `command_failed`
- `validation_failed`
- `replay_conflict`
- `unsupported_command`
- `unsupported_protocol_version`
- `malformed_payload`
- `internal_error`

Under the current `controller_serial_docker_exec` transport, this contract is
still filled by the controller-side batch executor. It should be understood as
the shape that a future local agent will eventually return directly.

The current implementation already has real behavior for:

- `ack.ack_status`
- `apply_result.apply_status`
- `apply_result.applied_commands`
- `apply_result.skipped_commands`
- `apply_result.failed_command`
- `error.error_code/error_category/error_message`
- `execution_nonce`
- controller-side `agent_health` placeholder

The following are still protocol reservation only:

- true local-agent `agent_health`
- durable replay detection and `already_applied`
- protocol/version/payload rejection before execution
- local-agent-side validation failures

## Replay / Idempotency

Replay semantics should be explicit for the same `node_plan_id`:

- today: replay detection is protocol-only; there is no persistent replay store
  and no true agent-side duplicate detection
- today: retries rely on best-effort command idempotency, command ordering, and
  command-level `idempotency_key` metadata
- future: a local agent should be able to detect same-`node_plan_id` replay and
  report `replay_detected`, `already_applied`, or `replay_conflict`

So the current implementation reserves replay fields and same-plan replay
behavior in the schema, but does not yet implement durable replay tracking.

The current controller-side executor therefore exposes replay semantics in the
response envelope, but replay detection remains declarative only.

## Node Metadata Boundary

The runtime update protocol should also define what controller-visible node
metadata is, without letting node metadata silently become a disguised global
topology dump.

Controller-authoritative examples:

- node type and intended forwarding role
- controller-visible interface role inventory
- forwarding/update capabilities that come from the modeled node role
- current scenario/topology/path/FEC decisions produced by the controller

Node-authoritative examples:

- local interface health
- neighbor reachability
- local forwarding/runtime state
- local agent health and ack/error details

Future `SatelliteNode` / `GatewayNode` abstractions inside skyseed should be
able to expose metadata such as:

- satellite: `node_type=satellite`, runtime forwarding role, interface roles,
  MPLS/LFIB support, runtime attach support, future control-plane endpoint
- gateway: egress/CGNAT role, `supports_bgp`, gateway cluster/region metadata,
  future control-plane connectivity to a ground control source
- UT: serving satellite, attachment state, and mobility/runtime-attach
  capability

The key boundary is that a `node_plan` should contain only node-local mutation
instructions and node-local metadata. It should not require the node to know
the full topology or full mini-Internet routing table.

## Transaction Semantics

The current serialized node batch executor should be documented as:

- ordered and replayable
- best-effort idempotent at the command level
- non-atomic at the node-plan level
- partially applicable on failure
- without rollback today

So the current failure boundary is:

- commands run in-order
- the batch stops on the first non-allowed failure
- commands after that point are skipped
- rollback is not attempted
- the result/ack schema must report partial application clearly

The current result lifecycle should be documented as:

- ACK lifecycle:
  `pending -> awaiting_transport_completion -> acknowledged_success|acknowledged_error`
- Apply lifecycle:
  `not_started -> in_progress -> applied|partially_applied|transport_error`

If a batch fails in the middle:

- `failed_command` should capture the first non-allowed failure
- command-level metadata such as command family and logical kind should be
  reflected in the error contract
- later commands should be recorded as skipped

For future local-agent work, the same response envelope should also reserve
non-execution failure paths such as:

- `validation_failed`
- `unsupported_command`
- `unsupported_protocol_version`
- `malformed_payload`

These are schema/interface reservations today, not active validation features.

This keeps current behavior honest while leaving room for future extensions
such as local-agent replay detection, persisted ack history, or more explicit
transaction policies.

## Serialized Runtime Boundary

The current runtime controller must be treated as:

`single-writer / serialized-operation safe only`

This means:

- all runtime mutation in the current demo and smoke flow assumes strict serial execution
- do not run parallel attach/release/connect/MPLS-update actions
- do not run multiple controller terminals or processes that mutate runtime state at the same time

The current boundary is safe enough for serial demo execution, but future
parallelism or scale-up would expose these risks:

1. Interface-diff identification risk
   Today the controller uses a before/after interface diff after `docker network connect` to identify the new interface. Serial execution typically adds exactly one interface. In parallel execution, multiple interfaces may appear at once, which can lead to wrong rename/IP configuration/link-state updates. The current principle should remain: `new_ifaces` must be exactly one, otherwise abort/rollback.

2. `link_state.json` write races
   The current state update model is still read-modify-write JSON. Parallel controller actions can overwrite each other. A future scalable design would need file locking, transactional updates, a single-controller event loop, or versioned state.

3. Rollback interference
   Under parallel execution, one operation's rollback may delete resources that another operation just created. A future design would need transaction IDs, per-slot locks, or per-node locks.

4. Slot ownership is too coarse
   Today a slot is mostly just `occupied=true/false`. Parallel orchestration would need richer states such as `free`, `reserved`, `connecting`, `occupied`, `releasing`, and `failed`.

5. Topology state and forwarding state can diverge
   Under parallel or partially failed updates, topology may already switch while Linux `MPLS` forwarding is still old, or only some nodes may receive the new `LFIB`. A future design would need two-phase update, make-before-break, or versioned forwarding state.

6. Label allocation conflict
   The current single-UT, single-LSP demo keeps label use simple. Multiple concurrent `LSP`s would require a per-node label allocator so local labels cannot collide.

7. Verify/`tcpdump` interference
   With multiple concurrent flows, `tcpdump` may capture packets from the wrong `LSP`. A future design would need unique flow IDs, labels, payload markers, or stricter capture filters.

8. Host sysctl is a global prerequisite
   `bridge-nf-call-*` and `net.mpls.platform_labels` are host/runtime prerequisites and should not be toggled freely by multiple concurrent tasks. They should be prepared and checked as runtime prerequisites, not treated as per-round mutable state.

9. UT dual-attachment semantics remain unresolved
   The current rule is still exactly one active `UT` access attachment: `0` means `ut_not_attached`; `2+` means `ambiguous_ut_attachment`. Future make-before-break support would require explicit `active/standby` or a session/handover state machine.

10. Multi-controller risk
   A future runtime design should consider a lock file or a single-controller daemon so multiple controller processes cannot mutate runtime state concurrently by accident.

For the current prototype, the operational rule is simple:

- keep runtime mutation strictly serial
- treat the loop runner and smoke wrappers as single-writer tools
- if an interrupt lands in the middle of a runtime mutation, explicitly rerun the target scenario or smoke wrapper to reconcile topology before continuing

## Runtime Demo Loop

The current demo loop is intentionally small and serial:

- `t0`
  - `ut1 -> sat-a1`
  - `gw1 -> sat-b1`
  - cross-plane `sat-a1 <-> sat-b1`
- `t1`
  - release cross-plane
  - detach `ut1`
  - detach `gw1`
  - `ut1 -> sat-a2`
  - `gw1 -> sat-b2`
  - cross-plane `sat-a2 <-> sat-b2`

The demo driver is:

`satellite/demo_runtime_handover.py`

It runs serially only and prints:

- current active links
- current occupied slots
- current runtime action
- `link_state.json` summary
- simple current-state reachability probes

The demo intentionally reuses the same slot names:

- `ut1-access-slot0`
- `gw1-gateway-slot0`
- `ab-cross-slot0`

So the probe IPs are slot-peer IPs rather than satellite-specific
access/feeder/cross-plane IPs. The main changes during handover are the
`binding`, container interfaces, and transient reachability, not the probe IPs
themselves.

This current demo only validates the slot-runtime link behavior and state
consistency. It does not exercise the newer `UT egress MPLS` or
selected-`GW` `NAT/general return` workflow, and it does not restore the old
forwarding baseline.

## Map Observation Boundary

- `SEED map` for this example is available at `http://127.0.0.1:8080/pro/map`
- for this phase, treat it as a compile-time topology visualization
- that compile-time view should now include the restored `B00 miniInternet`, the `AS250` satellite side, and the `AS65000` gateways
- do not rely on it as a real-time rendering of runtime slot bridge connect/disconnect
- runtime truth is still the controller output, container interface state, and `link_state.json`
- because forwarding/MPLS is intentionally out of scope here, there is no stable `UT -> ground host` end-to-end probe target yet
- the recommended observation style is:
  - keep pinging `10.90.11.1` from `UT` and watch `ut1-access-slot0` `binding.satellite_id`
  - keep pinging `10.91.11.1` from `GW` and watch `gw1-gateway-slot0` `binding.satellite_id`
  - watch transient down/up of cross-plane peer `10.92.1.2` from `sat-a1` or `sat-a2` while reading the `ab-cross-slot0` binding
- the current demo is meant to highlight:
  - runtime slot stability
  - dynamic link visualization
  - rollback safety
  - slot reuse
  - Docker network/interface cleanup

## Scenario Matrix

The current validated runtime matrix intentionally keeps only two explicit
scenarios:

`t0`:

- `ut1 -> sat-a1`
- `ut2 -> sat-a2`
- `ut3 -> sat-b3`
- `gw1 -> sat-b1`
- `gw2 -> sat-a3`
- `ab-cross-slot0: sat-a1(left) <-> sat-b1(right)`
- `ab-cross-slot1: sat-a2(left) <-> sat-b2(right)`
- expected outbound selected gateways:
  `ut1 -> gw1`, `ut2 -> gw2`, `ut3 -> gw1`
- expected `UT -> external` satellite paths:
  `ut1: sat-a1 -> sat-b1`,
  `ut2: sat-a2 -> sat-a3`,
  `ut3: sat-b3 -> sat-b2 -> sat-b1`

`t1`:

- `ut1 -> sat-a2`
- `ut2 -> sat-a3`
- `ut3 -> sat-b1`
- `gw2 -> sat-b2`
- `ab-cross-slot0: sat-a2(left) <-> sat-b2(right)`
- `ab-cross-slot1: sat-a3(left) <-> sat-b3(right)`
- expected outbound selected gateways:
  `ut1 -> gw2`, `ut2 -> gw2`, `ut3 -> gw2`
- expected `UT -> external` satellite paths:
  `ut1: sat-a2 -> sat-b2`,
  `ut2: sat-a3 -> sat-a2 -> sat-b2`,
  `ut3: sat-b1 -> sat-b2`

This matrix is only meant to cover:

- `UT -> external` via `gw1`
- `UT -> external` via `gw2`
- selected-gateway changes across `t0 <-> t1`
- path changes across `t0 <-> t1`
- serial runtime reconciliation of one explicit scenario at a time

## Mainline Model

The current mainline model is Starlink-residential-like outbound Internet
access, not public inbound reachability.

- `UT` traffic leaves through `serving satellite -> selected GW -> miniInternet`
- the selected `GW` applies the current minimal `SNAT/FORWARD` realization
- `external host -> UT` is not part of the baseline
- `UT -> UT` direct reachability is not part of the baseline
- `UT stable identity` remains useful as controller-visible identity/FEC state,
  but it is not a public service address in the current model

If future `UT <-> UT` support is revisited, it should be treated as a separate
operator-internal subscriber-routing/core design problem. It should not be
reintroduced as public inbound reachability, `anchor` ingress, or per-pair
shortcut state under the current baseline.

## Current Runtime Correctness Baseline

The current correctness baseline is therefore:

- apply `t0` or `t1`
- install outbound/current-gateway `UT egress MPLS`
- verify `UT -> miniInternet host`
- switch `t0 -> t1`, reinstall forwarding, and verify outbound reachability
  recovers on the new selected gateway/path

Recommended manual acceptance commands:

```bash
python3 satellite/controller.py --prepare-ut-egress-policy ut1 --timing
python3 satellite/controller.py --apply-scenario t0
python3 satellite/controller.py --verify-scenario t0
python3 satellite/controller.py --install-ut-egress-mpls ut1 --timing
python3 satellite/controller.py --verify-ut-egress-mpls ut1 --timing

python3 satellite/controller.py --cleanup-ut-egress-mpls ut1 --timing
python3 satellite/controller.py --apply-scenario t1
python3 satellite/controller.py --verify-scenario t1
python3 satellite/controller.py --install-ut-egress-mpls ut1 --timing
python3 satellite/controller.py --verify-ut-egress-mpls ut1 --timing
```

The current `UT egress` plan is now split into explicit categories:

- `static_guardrail`: source-rule skeletons, aggregated
  internal/control/runtime guardrail prefixes, and cleanup of legacy
  fine-grained exclusion routes; run once or infrequently via
  `--prepare-ut-egress-policy` or `--prepare-all-ut-egress-policies`
- `dynamic_path`: the `UT default route` plus the serving-satellite
  policy-table `default egress route`
- `dynamic_lfib`: `MPLS input` and `LFIB` entries for the current selected
  gateway/path
- `dynamic_gateway_nat`: `FORWARD` / `SNAT` / reverse-route state on the
  current selected gateway
- `verify`: route/rule/`LFIB`/`iptables` inspection and dataplane probes
- `cleanup`: removes only dynamic items while keeping the static guardrail
  framework in place

The current example/runtime scope does not validate:

- `UT -> UT`
- `external -> UT`
- public inbound addressing
- `anchor`-based ingress designs
