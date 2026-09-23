# C/eBPF DataPlane contract

This reference describes the current BPF-specific `DataPlane` authoring type and
`BpfProgram` selection. The general data-plane concept also includes Linux native
forwarding, which shares the [pipeline](../architecture/overview.md#choose-a-forwarding-mode)
but does not use this C/map/header contract. Public API names are unchanged.

The BPF backend provides structured routing data, generated C access support,
node-level program selection and fixed local-output ingress. The authoritative logical result contract is
[Planner routes](planner-state-and-plans.md#routes); this topic owns declaration,
translation, attachment and C/object compatibility. Start with the
[copyable threshold example](../../templates/dataplane/threshold/README.md).

Select `BpfProgram(path, data=definition, authoring_directory=directory)` through
`dataplane_program=` on `ExperimentController` or `Producer`. Omission keeps the
packaged default and unchanged Example configuration. A program/schema selection
is fixed for the material chain; continuation changes values without recompiling.
`prepare_program(program)` performs the same offline preparation independently.

## Define, generate, implement, check, prepare

```python
from pathlib import Path
from skyseed.backend.bpf.dataplane import RouteData, Record, U32, BpfProgram

data = RouteData("choice", rule=Record(threshold=U32),
                 candidate=Record(weight=U32), max_candidates=2)
header = data.generate_headers(Path("authoring/generated"))
program = BpfProgram(Path("handler.bpf.c"), data=data,
                     authoring_directory=header.parent)
```

`generate_headers` is a core DataPlane API: it needs no Plan, Model, C source or
compiler. It writes `choice.h` to the chosen stable directory and does not rewrite
unchanged content. Normal preparation calls this same API. Include `choice.h` in
the entry translation unit. It includes `skyseed.h`, declares `choice_route`,
`choice_candidate`, nested records, managed maps and access helpers. Never copy the
generated structures into C or hand-encode bytes in a RouteModel.

Later, a RouteModel returns `LocalRouteOutcome(demand, disposition, candidates,
data)`. Its `PolicyRecord(threshold=128)` and each candidate's
`PolicyRecord(weight=100)` supply values, not layouts. Candidates contain
`LogicalExit(connection_id, source_node)`. The framework resolves those logical
references, encodes declared fields and produces map deltas. It assigns no meaning
to threshold, weight, priority or flags; only the selected C interprets them.

`choice_lookup(ctx, packet_target)` reads the acquired generation's current rule;
the key is an IPv4 target in packet byte order. `choice_candidate_at(rule, index)`
checks both the declared capacity and current count. `sky_data_active(ctx)` checks
network ingress against active managed ports; local ingress is active when the
node has an acquired, populated generation version. `sky_forward(ctx, exit)`
resolves the live interface and redirects, dropping if unresolved. `sky_pass(ctx)`
and `sky_drop(ctx)` report generic dispositions, with no delivery claim. Generated
rules expose `SKY_FORWARD`, `SKY_DROP`, `SKY_PASS`; the handler must interpret them.
Framework compatibility checks do not verify its policy or chosen redirect.

Run [development checks](../../tools/development/README.md) from an explicit Python
driver. Then prepare the experiment normally with the same program selection.
Checks are optional and produce no certificate required by ordinary execution.

## Supported representation

| Definition | Planned value | C/encoded representation |
|---|---|---|
| `U8`, `U16`, `U32`, `U64`, `I32`, `I64` | exact Python `int` in range | corresponding fixed-width integer, little endian |
| `BOOL` | exact `bool` | one byte, 0 or 1 |
| `EXIT` | `LogicalExit` at the arrangement's source | logical port u32 (1…0xfffffffc), resolved to a live interface at packet time |
| `NODE` | known `NodeId` | framework IPv4 packet identity, four network-order bytes; usable as a packet target |
| `Record(...)` | matching `PolicyRecord` or mapping | declared field order, natural BPF alignment, zero-filled padding |
| candidate collection | ordered `RouteCandidate` tuple | u32 count and fixed-capacity array; unused entries zero-filled |

Empty records occupy one reserved byte. Generated `sizeof`/`offsetof` assertions
and the encoder use the same definition. Nested records are supported; arbitrary
Python objects, strings, floating-point values, variable-length payloads and general
map plugins are not. Use explicit integer units/fixed point as needed. Field names
must be nonreserved C identifiers. Capacities and map sizes must fit u32; actual
kernel map allocation, verifier and stack/instruction limits still apply. They
are not inferred from the first Plan and no arbitrary scene/file-size limit is
added. Execution requires the little-endian material ABI.

For different definitions at different nodes, declare nested business
records and select one with `RouteData(..., value_field="name")`. This explicitly
selects that member of both rule/candidate `PolicyRecord` values. Only the selected
record must match the schema; full logical values remain in Plans. This avoids
putting program IDs or backend layouts in the RouteModel.

Custom structured programs consume explicit local arrangements. Complete-path
results supply no custom record fields: a covered complete-path forwarding entry
with such a program fails preparation with a compatibility error. The packaged
default keeps complete-path behavior and also accepts local zero/one-candidate
forward, drop and pass arrangements without business fields. Multipath or business
fields require a selected generated-data program. Calculated unreachability and
omission remain distinct planning facts; neither creates a managed forwarding
entry. Custom data receives no invented terminal/local-delivery rules.

## Node selection

`DataPlane((DataPlaneBinding(program, AttachmentScope(nodes=(node,))), ...))`
selects one forwarding program/data profile per covered node. A binding can cover
multiple nodes; different bindings can select different programs for different
nodes. Overlapping node selections fail preparation, even for the same program.
`AttachmentScope` accepts nodes only; connection-endpoint selection is removed.
Put conditional behavior inside the node's C handler.

The same loaded object, managed tables and node-owned mutable maps serve all of
that node's current/future managed network ingress interfaces and its fixed local
ingress. No dispatcher, local-entry selector or automatic program choice exists.
An empty `DataPlane()` or empty scope is valid; uncovered nodes receive no fallback
program or local injection. Bindings are fixed across deliveries. Existing default
Example assembly still selects the packaged program on every node.

For locally originated business traffic, the framework's fixed veth path invokes
this same handler before a real network exit is chosen. The default handler uses
full paths and supported local zero/one-candidate arrangements at the source just
as at other nodes; missing/empty/unreachable forwarding entries drop. Structured
handlers receive their authored local records/candidates. No access-peer helper,
reverse path or missing demand is invented. The existing custom-profile restriction
on full paths without schema-compatible custom fields still applies.

Local destinations are delivered by Linux's priority-zero local table before
injection; ordinary ground/management destinations retain their normal paths.
PASS returns to Linux without recursively entering local injection; it does not
promise delivery or create a Linux forwarding route. See the exact
[fixed ingress and ownership contract](compiler-and-runner.md#fixed-bpf-local-output-ingress).

## Handler and shared completion

Include `skyseed.h` in the entry translation unit, write an
`int handler(struct sky_context *ctx)`, and use `SKYSEED_PROGRAM(handler)` once.
The macro defines `skyseed_tc` in section `tc` and its GPL license. The framework
captures ingress facts, acquires a published generation, resolves the ingress
logical port or local-origin marker, calls the handler, emits optional telemetry, releases the reader,
and returns the handler's TC action. Ordinary early returns remain inside this
completion path. The handler may be inline or a BPF subprogram.

The context provides the original `skb`, acquired `generation` (or
`SKY_UNKNOWN_GENERATION`), and `ingress_port` (zero when unresolved, `SKY_LOCAL_INGRESS` for local output).
`sky_is_local(ctx)` tests that origin. The marker is not a PhysicalWorld connection,
active network port or selectable exit; `sky_ifindex` rejects it. Treat these
as read-only facts. The framework always calls the handler, including when
acquisition or ingress resolution fails; the handler chooses its policy.
`sky_ifindex(port)` resolves a logical output port to the current live interface,
or zero. `sky_redirect(ctx, ifindex, flags, reason)` calls `bpf_redirect` and
reports output/reason metadata; `sky_result(ctx, action, reason)` reports a reason.
A plain TC action return is valid and leaves result metadata unknown.

Authors own packet bounds checks, algorithms, mutable state and kernel-verifiable
code. Do not modify framework maps/configuration or retain generation-dependent
references beyond the call. A **successful BPF tail call**, a non-returning transfer,
or replacement of the framework entry bypasses completion and violates the
contract, potentially leaking a generation reader. Calling `bpf_redirect` itself
returns normally and is compatible; return its result to the framework. Nested
ordinary C function calls and early returns are compatible. The framework does
not prove this discipline by examining source or instructions.

Generation acquisition retains the existing pin-then-recheck protocol: increment
the generation's shared reader counter, reread publication, release/retry if it
changed, and access generation data only after successful acquisition. Three
attempts bound that existing race protocol; failure exposes the unknown-generation
sentinel. Continuing runs release the reader after observation on every normal
return. Finite runs need no reclamation counters. Retirement remains Runner-owned.
All loaded objects reuse the **same actual publication and reader-counter map
FDs**, not separately created maps with equal names.

## Object preparation and compatibility

`BpfProgram` accepts one `.c` or `.o` path, or a tuple of them. Local includes and
`include_dirs` are supported. Multiple translation units are compiled separately
and linked by libbpf. Include the framework definitions in only the entry unit;
use ordinary local headers for helper declarations and their argument types.
A compatible precompiled object needs the same entry, configuration declaration
and framework map layouts as an object built with this header. It can come from
another build system. Object input does not invoke Clang.

Material preparation compiles C with Clang (`bpfel`, `-O2`, `-g`), inspects the ELF
through libbpf without loading it into the kernel, and embeds the object bytes,
SHA-256, declarations and provenance in the sealed foundation. Clang's dependency
output records hashes of transitive source/header dependencies, including system
and framework headers, plus the compiler identity and commands. Precompiled
inputs record their object hashes; their unavailable source history is not
invented. Prepared objects are reused within the build and across continuation.
Execution needs neither the author's modules/headers nor their source tree.

Materials are **v8**, encoding `prepared-forwarding-v8`, magic `SKYRUN8`, with foundation
contract `skyseed-tc-elf/3` and C ABI 2. The outer v8 codec remains shared
with Linux; the BPF foundation rejects old program/selection/action contracts. Earlier materials require regeneration.
The framework validates the declared entry/section/type, configuration ABI,
required map layouts, FD/capacity bindings, artifact integrity and resource
operations. libbpf handles ELF relocations/BTF and object loading. Compiler,
linker, libbpf and kernel diagnostics remain visible. There is no arbitrary
source-size, instruction-count, node-count or elapsed-time validation ceiling.
The framework does not validate routing algorithms, correspondence between
reported output and route values, reachability or network coverage.

## Maps and runtime ownership

Framework maps supply generation publication/readers, ingress/egress binding and
optional telemetry. Their names/layouts are reserved; the loader binds capacities
and reuses runtime-owned descriptors before `bpf_object__load`. Telemetry and
reader maps are disabled through libbpf's autocreate setting when their constant
configuration branches are disabled. The kernel still validates the program.
The existing privileged helper, namespaces and TC attachment path are reused:
one program object per covered node, attached to its managed network ingress
interfaces and fixed local ingress. Bindings are updated when interfaces reappear.

The private default profile and generated structured profile share generation
versions, active-link tables and logical/live binding mechanisms. Structured
route-map values use the declared record size, rather than the default single-u32
exit. Material rows identify their actual node/table instance. Capacity comes
from encoded covered contents, with the selected profile reserved at each covered node; no tables are populated on uncovered nodes. Field-only changes produce
new immutable versions. Unchanged values reuse encoding and unchanged table versions
remain shared. Empty/omitted arrangements remove earlier managed entries.

Other declared BPF maps (and ordinary object globals) are **program-owned mutable
state**. libbpf creates them per node object; they persist across forwarding
generations/deliveries until Runner cleanup. They are not immutable snapshots and
reclamation never deletes their entries. Automatic external pinning is rejected:
Runner retains the object, drains owned network resources, removes owned pins and
closes its object and descriptors, including after partial initialization failures.
Map/program capacity and allocation failures retain their normal diagnostics.
The capacity audit's `pinned_object_count` counts the shared generation map, enabled
reader/telemetry maps, two binding maps per node, and each covered node's
entry program and managed-profile maps. Object-owned mutable maps and globals
are not pinned and are excluded.

Batch writes and their capability fallback, unchanged-version reuse, future
prewrite, fast generation publication, logical-to-live binding, continuation and
safe reclamation remain framework mechanisms. The same generation/attachment coordinator owns these mechanisms. Batch updates
support each declared fixed value width, with the existing capability fallback.

## Observation semantics

Telemetry remains normally enabled with Traffic and disableable through existing
Traffic/Runner controls. It is part of the framework entry, not a second TC program
attached after a terminating handler. The same ring collector and per-node loss
counters remain in use. Observation failure does not change the handler's action.

Event schema `skyseed-tc-ingress-v3` is 56 bytes, little endian. It distinguishes
facts observed **before** the handler (timestamp, node, ingress ifindex, packet
length, available IPv4/ICMP fields), acquired generation, returned `tc_action`,
and **handler-reported** selected output/reason. Missing IPv4 facts decode to
null. Fact bit 1 records local origin; its decoded observation is `local-output`,
with `local_origin=true`. The internal ifindex is a raw fact, never a network
connection/peer identity or additional hop. The source decision can be observed;
arrival at the selected peer still requires that peer's ingress observation.
Traffic requires explicit boolean `ipv4_observed=false` for null source and
destination addresses; observed IPv4 and legacy records still require both
addresses. Non-IPv4 and truncated observations remain in replay packet evidence
and packet counts, with unknown fields intact, without creating ICMP transactions.
ICMP validity remains explicit. Unknown output is null; unknown reason is
`unknown` with the numeric `reason_code` retained. Reasons 1–8 retain the default
handler's meanings; zero means unknown and other byte values are author-defined.

Decision `pass` means `TC_ACT_OK`, never confirmed local delivery. Traffic only
uses explicit `local-route` metadata for the new schema's endpoint claim; legacy
records retain their historical reading. Actual probe replies remain independent
evidence. Missing events and ring-output losses remain telemetry loss, not proof
of network packet loss. Local arrangements, including drop/pass, do not establish complete paths. Planner
and Traffic readers show their disposition/data or explicit unknown reachability;
active probe selection skips combinations without the required complete-path evidence.

## Dependencies and validation boundary

Build/material preparation from C requires Clang/LLVM with BPF support, Linux
UAPI headers, libbpf development headers, and libbpf 1.x runtime for inspection
and linking. Ubuntu packages: `clang llvm libbpf-dev libelf-dev linux-libc-dev`.
The existing `skyseed_dev` Python remains the authoring environment. A standard
system installation or toolchain on PATH is sufficient; Python's environment
prefix include directory is also searched. Compatible `.o` inputs need only
libbpf at preparation, not Clang or development headers.

Execution requires the installed SkySEED Python package and host `libbpf.so.1`
(the same library used by the existing collector), plus the existing Linux/BPF
and Docker capabilities. It never compiles C. Packaged C/header files support
future builds but are not read by Runner. See [Linux host](../user-guides/linux-host.md).
The implementation follows the upstream [libbpf API](https://libbpf.readthedocs.io/en/v1.3.0/api.html).

Stage 2B originally covered real C compilation/linking, libbpf open/declaration
inspection, native execution with helper substitutes, and mocked runtime resources.
Later retained Tiny BPF runs exercised kernel loading, fixed local ingress,
telemetry, Traffic, pause/resume/reset and owned cleanup. The completed 30-second
reset invocation recorded 145/145 sent/received probes; its saved output was later
restored and re-rendered without another execution. [Current validation](../CURRENT.md)
identifies those retained records and their original source conditions.

That small default-profile evidence does not qualify every authored C program or
structured schema, establish scale/performance equivalence, or constitute final live
or visual acceptance. Custom structured encoding/selection remains covered by the
focused offline checks; historical runs do not automatically qualify later changes.
