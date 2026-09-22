# Compiler, execution materials and Runner boundaries

Both backends share one Plan → Compiler → sealed materials → Runner pipeline.
[Assembly](../api/assembly-and-control.md#forwarding-selection) selects a backend;
[Planner](planner-state-and-plans.md#routes) owns logical results;
[execution architecture](../architecture/execution.md) explains independent owners.
This topic owns representability, handoff and realized resource ownership.

## Compiler and sealed materials
`compile_plan(plan, physical_world, previous_result, forwarding_backend="bpf")` validates scene/lineage/cadence and
lowers one semantic replay into backend-identified `CompilationResult`. It never reruns a Model. Results
retain exact activation timestamps, output planned state, backend intentions, constraints
and predecessor indexes. Empty transitions still create boundaries.

Existing activation frontiers, Runner phases and (for BPF) atomic forwarding generations discharge
precedences where sufficient; otherwise exact pairwise constraints remain. The material
writer invokes the selected backend translation and owns normalization, capacity accounting,
codecs and seals. Its compact summary is
published without rescanning or round-tripping all output. History's compiler projection is
not a replacement for the authoritative binary execution payload.

Current contracts are material **v8** (`prepared-forwarding-v8`, magic `SKYRUN8`),
outer execution definition **v6** (`skyseed-v2-generated-forwarding-execution`),
BPF foundation **`skyseed-tc-elf/3`** and Linux foundation **`skyseed-linux-ipv4/2`**.
The execution reader also accepts existing **v5 definitions without a preparation hook**;
this does not permit older material/foundation schemas. Earlier incompatible artifacts
must be regenerated, never interpreted through a legacy execution path. The
[preparation contract](experiment-control.md#initial-readiness-command) owns the optional
v6 command and its captured-function adapter. Compiler inspection history is separate.
A terminal artifact seal completes a finite delivery;
`delivery.final` identifies the last delivery in a finite chain. Retained sessions publish
nonfinal deliveries. The delivery record carries index, predecessor manifest hash,
end/Plan/state/generation and cadence. Nodes are fixed; only an append-only contiguous suffix
is admitted. No replacement of an already loaded future is supported.

## Supported route expressions
Both modes consume the same RouteModel values. BPF can encode the declared local
candidate/policy structures for its selected handler, as specified by the
[BPF customization contract](dataplane.md#supported-representation). A value legal to
Planner is not necessarily representable by every backend.

The shared Compiler validates logical references, deduplicates shared demands
and expands `ReachableRouteOutcome` paths into exact per-hop bindings. Native material
translation consumes those bindings. For Linux only, an unconditional single-candidate
local FORWARD normalizes to the same binding as an equivalent full-path hop before
shared-demand ownership checks; the original Model outcomes and BPF records stay intact.
Destinations are existing GS/Satellite node and Zone service identity /32s;
next hops are the peer's prepared native link IPv4 address, with the actual output
ifindex bound at execution. No permanent final-destination neighbour is installed.
Unicast routes carry `RTA_PREFSRC` equal to the source node's stable identity. This
helps replies follow computed node/Zone return routes when those routes exist; it does
not synthesize a reverse path or provide routes to arbitrary unplanned endpoints.
Explicit socket-bound sources remain the caller's choice; forwarded packet sources
are not rewritten.

`LocalRouteOutcome` is an exact source/target arrangement, without completing any
surrounding path. FORWARD supports exactly one candidate and empty outcome/candidate
`PolicyRecord` data, using that candidate's `LogicalExit`. PASS contributes no native
override and withdraws a previous owned override. Explicit DROP installs a blackhole
route. Empty/multiple forwarding candidates or any nonempty arbitrary policy record
are rejected during offline native material preparation; no weight, priority, ECMP
or custom C semantics are inferred. Such results remain legal Model values for a
compatible BPF definition. A native FORWARD/DROP for a destination local to its source
is unrepresentable without overriding the local table and is rejected offline. This
also applies to unchanged arrangements when a Zone migration makes the destination
local. PASS and existing node/Zone address ownership handle local delivery.

`UnreachableRouteOutcome` retains its shared meaning: no forwarding binding. Missing,
empty, unreachable and PASS outcomes remove superseded owned overrides; none fabricate
blackholes. Only explicit DROP does that. Shared compilation resolves demand ownership
and rejects conflicting arrangements. Supported node and scoped Zone identities map to
distinct native destinations; native translation retains only the prior route per
source/destination and emits changes to effective bindings.

## Shared Runner resource lifecycle
Sealing a delivery makes input immutable; it does not make the corresponding network active.
One successor can be admitted/prepared while Execution retains the last complete ACTIVE state.

Constructing Runner acquires no network. `prepare()` validates initial materials,
discovers deployment incarnations, acquires the selected adapter’s helper/resources,
prepares the first delivery and activates boundary zero. BPF additionally binds maps/ELF
and prewrites tables; Linux validates and prepares ordered operations. `run()` starts the logical clock and advances from boundary one.
Execution creates current resources, establishes readiness, applies selected forwarding,
then withdraws obsolete resources and joins deletion before exposing formal ACTIVE.
Future interfaces do not exist merely because future forwarding was prewritten.

One successor may be pending, preparing or READY. Maintenance loads, binds and prewrites
it; only Execution activates it. BPF reservations derive from the first batch and fixed
`batch_capacity_factor` (1–16, default 2), using typed per-node peaks to allow peer work migration.
The extra reservation has a memory cost; exceeding it fails admission. Preparation starts
according to `batch_lead_fraction` in (0,1], default 0.75. Neither setting promises readiness
by a wall-clock target. Missing or late READY holds the last complete ACTIVE state and shifts
the execution clock when released. Material waiting is independent of user pause.

Maintenance reclaims retired batches and, for BPF, immutable node versions only after actual activation,
reader accounting and retained references permit it. Monotonic generation identities avoid
ABA reuse. Execution uses retained privileged sockets/syscalls rather than per-action Docker
or shell commands. Cleanup drains owned mutations and evidence and releases resources;
uncertain ownership is not silently discarded. Slow preparation/cleanup is not timed out.

## BPF preparation and realization
The [BPF DataPlane package](../../src/skyseed_v2/backend/bpf/dataplane/README.md)
provides offline definition/translation and Runner-owned runtime adaptation. `BpfProgram`
selects C sources or compatible ELF at assembly; omission selects the default. Material
writing prepares each selected object once, resolves logical exits, encodes changed
node-version tables and seals executable actions. Existing `compile_bpf` and BPF result
aliases remain public compatibility names for the shared Compiler.

Runner binds actual namespaces, interfaces and shared map descriptors, loads the sealed
ELF through libbpf and prewrites bounded table chunks. BPF publication and reader accounting
permit retirement only after all readers release prior generations. No C compilation,
author-module import or intention expansion occurs during execution. C/header/schema/map
contracts, ABI versions and preparation dependencies are maintained only in the
[BPF customization reference](dataplane.md). Shared kernel primitives live in `backend/linux`;
native Linux execution does not import this BPF adapter or toolchain.

## Fixed BPF local-output ingress
Preparation creates `sv2localout`/`sv2localin`, one owned veth pair inside each
covered node's namespace. The selected node program attaches to `sv2localin`
and its managed network ingress interfaces, sharing the same object and mutable
maps. The fixed ingress binding uses `SKY_LOCAL_INGRESS`; there is no egress
binding, logical connection, Planner resource or retirement cohort for the pair.
Its binding capacity is reserved separately from dynamic network ports.

Fixed policy rules use `iif lo` and only the allocated node-address range plus,
when Zones exist, the framework's reserved `198.18.0.0/15` pool. Rule priorities
1 and 2 follow the ordinary priority-zero local-table lookup. The rules select
owned table `0x535632` with route/rule protocol 253. One route per range steers
via on-link gateway `169.254.0.1` on `sv2localout`; one permanent common-MAC
neighbour serves both ranges. The gateway is only a neighbour key, not an assigned
node address or business destination. Pair/rule/route/neighbour counts are fixed
by these ranges and do not depend on route outcomes or individual destinations.
No per-business-destination Linux helper or implicit access-peer route remains.

The route's preferred source is the stable node /32 on `sv2identity`; explicitly
bound source addresses, including owned Zone /32s, retain their identities. The
local table handles destinations owned by the node before steering. Traffic to
ordinary GS ground/management networks falls outside the reserved ranges. A
packet returned with PASS enters Linux with `sv2localin` as its input, so it does
not match `iif lo` again. Forwarded network ingress likewise does not match.
This follows the upstream [ip-rule iif contract](https://man7.org/linux/man-pages/man8/ip-rule.8.html).
PASS has its ordinary stack-processing meaning and supplies no route or delivery
promise; Linux policy/firewall configuration still applies after the handler.

Startup inventories must find the ordinary priority-zero local rule, unused
reserved rule priorities/table, no reserved interface names, and no static
address/nondefault-route overlap with the steering ranges. Explicit conflicting
configuration rejects preparation; Runner does not renumber, replace or flush it.
The covered namespace must already have `net.ipv4.conf.all.rp_filter=0` (the
ordinary SEED defaults); the adapter checks this without overwriting global
settings. Only the owned local ingress enables forwarding/accept_local and
turns off reverse filtering and redirects. No IP address is assigned to the pair.

Exact pair, route and rule ownership is retained before each mutation, including
uncertain acknowledgements. Exclusive-add EEXIST disclaims that conflicting
resource. Cleanup removes recorded steering rules first, then recorded routes,
then the pair (which removes its neighbour and attachment), before identity/maps
are released. Failures retain the ledger for retry; unrelated rules, routes,
interfaces and addresses are never broadly flushed. A new invocation prepares
fresh owned resources after successful cleanup. Setup is reused across route
changes, connection replacement and delivery continuation; activation changes
only declared BPF data, dynamic links/qdiscs and owned Zone addresses.

The fixed-ingress preparation audit shares one table-aware IPv4 route inventory
between address-collision and reserved-table checks; rules are inventoried separately.
Address collisions exclude default prefixes. No persistent route cache is added.

## Native IPv4 foundation
Both backends retain the deterministic NodeId-ordered IPv4 identities from the
smallest enclosing range within `100.64.0.0/10`, and the existing Zone packet
identities. Runtime now creates its own persistent **`sv2identity` dummy** on each
node. Stable node /32 and active Zone /32 addresses live there. SEED `dummy0`,
loopback, static interfaces, addresses and protocol configuration remain owned by
SEED. The dummy persists through delivery preparation, activation and continuation;
cleanup deletes only the run's dummy. A pre-existing reserved interface is an
initialization error, never a cleanup target.

Linux dynamic veth pairs receive separate /31 IPv4 subnets, allocated offline in
first-appearance order from `172.16.0.0/12`, `10.0.0.0/8`, `192.168.0.0/16`, then
`100.64.0.0/10`, excluding node/Zone allocations. The retained allocation ledger
keeps the same connection's addresses on recreation and across delivery seams;
it checks interface-name and subnet collisions. Pool exhaustion is an IPv4
allocation error, not a scene quota. Initial namespace address/non-default-route
inventories detect identity collisions. Compilation and both runtime identity audits
index fixed IPv4 exclusions once as merged ranges; overlap queries do not scan every
Zone for each link or namespace prefix. The retained static-resource index checks new
link allocations against static resources during preparation, without repeating
whole-network inventories. An overlap fails clearly; runtime never renumbers
sealed materials or takes over the colliding static resource.

SEED node options prepare forwarding and disabled global reverse-path filtering
for the native deployment; explicit incompatible options are rejected. Runtime
checks the global reverse-path prerequisite and sets IPv4 forwarding, disabled
reverse-path filtering and redirects only on its new interfaces through netlink.
It does not reset global sysctls or alter static interface settings. Each link uses
ordinary kernel MAC allocation, connected subnet routes and ARP, without the BPF
common-MAC/per-destination permanent-neighbour scheme. Framework unicast routes request the source node's stable address for locally
originated packets. Other Linux traffic uses normal kernel source selection;
applications can bind their own source explicitly. No reverse route or remote-node
reachability is invented.

Linux `prepare()` validates the backend/format and executable schema before helper
acquisition, binds namespace resources, creates stable identities, prepares the
first delivery and activates B0. Future preparation only validates and serializes
prepared operations; future links and Zone addresses do not appear early. It needs
no Clang, libbpf, BPF maps, pins, or program attachment. The shared helper transport
selects its native command module without importing the BPF adapter. Before retaining
namespaces, the native helper reserves a soft descriptor limit of `2 * node_count + 64`
(one namespace FD and route socket per node, plus transient/control headroom). It
leaves the hard limit unchanged and rejects insufficient hard capacity before
acquiring namespaces or mutating the network.

The native artifact reader validates every external request's schema, IPv4 fields
and boundary timestamp before returning its cached boundary sequence. That sequence
also retains the parsed link subnets. Runtime preparation consumes these values
without repeating schema/IP parsing; it checks chronological suffixes, connection
identity and static collisions, active-link/Zone state, and membership in the
foundation's retained Zone-address set. These checks apply to every delivery.

Activation executes ordered link, directional-parameter and Zone-address changes.
Unchanged links retain their actual devices. ACTIVE advances only after every
operation completes; events report `ordered-linux-operations`, a completed activation
ordinal and no BPF generation publication. A failure may leave a partial network;
it keeps the last completed frontier and cleanup ownership rather than claiming
rollback or atomicity. Initialization failure, partial activation, cleanup retry
and rerun use the same owned-resource ledger. Native `generation_count` /
`prior_generation` in the shared delivery envelope count chronological activations;
they do not identify a BPF generation. Native preparation has no BPF map reservation
or capacity-factor admission limit.

The shared lifecycle above applies unchanged. Native preparation has no BPF map
reservation/capacity-factor admission limit. The read-only `NativeRoutingEnvironment`
used by Compiler is also available for [assembly-time daemon configuration](../api/assembly-and-control.md#native-routing-software).
Runner receives sealed foundations and operations, never an authoring callback or protocol policy.

## Native route ownership
Owned records use ordinary **main-table (254), IPv4 /32, TOS 0** routes with private
**protocol 253 and fixed metric 1**, universe scope and unicast/blackhole type. Protocol
is an ownership label, not route preference. Metric 1 is a fixed private key, not a
Model policy field or source-arbitration mechanism; a nonzero metric is necessary
because the kernel's delete matcher treats zero as unspecified. These private
ownership values are reserved for the invocation's framework records. Normal kernel
routing rules still apply, including local-table priority and other routes at different
metrics. A prepared framework route is not proof of the effective protocol route,
actual packet path or reachability. No routing rules, other tables or priority
selection API are added.

Installation uses exclusive create; an occupied exact destination/TOS/metric key
fails without replacing or claiming the existing route. Changes withdraw the precise
owned predecessor, then add the new record exclusively. Deletion includes destination,
table, TOS, nonzero metric, protocol, scope, type and, for unicast, retained ifindex,
gateway and preferred source. An absent exact record (including one expired with a
veth) is tolerated, without broadening the delete to a foreign replacement. No FIB
flush or repeated FIB inventory is used. See the kernel's
[IPv4 insertion/deletion matching](https://github.com/torvalds/linux/blob/v6.8/net/ipv4/fib_trie.c)
and [next-hop matching](https://github.com/torvalds/linux/blob/v6.8/net/ipv4/fib_semantics.c).
Routes attached to a removed device naturally expire with that device; they cannot
outlive an owned veth teardown.

Link/address readiness precedes route installation. Route changes complete before
obsolete Zone addresses and links retire. A Zone move can transiently have both local
addresses while main-table changes run; only the completed boundary is ACTIVE. Future
preparation exposes no routes or links. Change actions preserve predecessor identity,
validate active connections and use the artifact reader's already checked values;
no route selection or repeated schema/address validation is performed by Runner.
Execution uses retained namespace/netlink sockets. It is ordered work, with a possible
withdraw/add gap and partial failure, not atomic generation publication.

The helper records exact route requests before sending, retains uncertain ownership
on lost acknowledgements, and disowns definite exclusive-create collisions. Cleanup
withdraws only that ledger before owned link/identity teardown and remains retryable.
Routing daemons remain node payloads, outside this ownership ledger. Observed packet
forwarding and protocol convergence require their own evidence.

## Traffic lifecycle and observation
[Observation Tools](../user-guides/outputs-and-tools.md) identify the recorded backend,
not inferred route coverage. Compiler/Runner facts do not establish protocol routes or
packet delivery; native BPF-only measurements remain unavailable. Existing artifact paths
and historic timing-label reads remain compatible.

Traffic plans are explicit optional workloads built from Plans and compiled reachability.
The generation Tool owns ping processes through experiment orchestration; Runner owns
network state, time and its independently enabled BPF telemetry recorder.
Arming, migration, rearming and classification follow actual completed ACTIVE frontiers,
not planned timestamps or ideal wall-clock targets. A blocking source stop ends the old
launch decision: reconciliation rereads published ACTIVE state before choosing a
replacement, including no-service, episode-end, hold and cancellation outcomes. Planned unreachable, observed reachable,
activation transience and unexplained reachable-path loss remain distinct.

Experiment execution stops Traffic and joins its teardown before Runner cleanup; outer
managed execution then calls Deployer cleanup. Runtime reset reuses retained materials and
Compose while creating a new invocation. Observation streams identify their own recorder
origin and reference generation measurements, rather than copying them into every run.
Optional reporting failure cannot replace successful domain execution. An explicitly enabled
active Traffic operation failure remains an execution failure.

## Applied connection observation
`runner/connection_observer.py` is an optional private bounded nonblocking sink,
instantiated by experiment execution only for a prepared Map deployment. Native and
BPF adapters return normalized connection deltas only after the privileged mutation
result confirms the complete target transition. Runner submits those acknowledged
deltas and the already discovered node/container bindings. Future prewrite and
preparation supply no live connections. The worker retains only current links.
Its private v2 handoff writes `connections.json` only when topology changes, before
publishing the referring `.control/internet-map/live.json` status. The latter is a
small heartbeat with invocation, event/topology revisions, availability and actual
activation frontier; idle, pause and status-only updates never traverse or serialize
the connection table. The worker never scans containers,
rereads histories or calls the viewer. The internal fixed BPF veth is not emitted.

Pause/material wait preserve current links. Removal/recreation updates their applied
identity; reset starts a new invocation. Partial failure or queue loss invalidates
observation instead of asserting that a target succeeded. Successful runtime cleanup
publishes an empty cleaned state. Writer errors cannot govern forwarding; the Map
marks a missing/stale nonterminal heartbeat unavailable. Experiment joins the
observer after Runner cleanup; no viewer request is part of activation.

The separately adapted local SEED Map reads this optional handoff through `/api/v1/live-connections`. A client
sends its known invocation/topology version: status and staleness are checked on
every request, but matching content is neither read nor returned. A mismatched
status/content publication is unavailable, never a guessed topology. The frontend
skips graph processing for omitted unchanged content and resets its known version
after unavailability or a graph rebuild. It patches only dynamic edge DataSet items,
preserving node layout and terminals. Valid
complete tcpdump records flash their source container; stream fragments and startup
errors are separated without a Docker MAC requirement. Dynamic link activity and
packet paths are not inferred. Capture stays on demand with existing filters.

Deployer stages the installed checkout's `tools/InternetMap2` as a content-addressed
Compose build context with a read-only handoff directory and project scope. This
builds the installed source; the extra endpoint/UI support requires the local Map adaptation,
not merely an arbitrary upstream checkout. Ordinary SEED has no required handoff or scope;
its published-image behavior is unchanged. No new telemetry platform is introduced.
The native S4 Example exposes `NativeRoutingEnvironment.node_addresses` through existing
SEED node descriptions, which Map already renders as `Des`. This is Example-owned metadata,
not a new interface, address allocator, scan or Map/Runner contract. Zone service addresses
remain separate; the existing Settings → Drag Fixed option controls node placement locally.
