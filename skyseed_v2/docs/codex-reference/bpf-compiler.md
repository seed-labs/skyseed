# BPF Compiler contract

## Authority

Normative detail under `../accepted-design.md`. Implementation state is
recorded only in [`CURRENT.md`](../CURRENT.md). Simulator `Plan` and retained
Slice facts remain upstream semantic authority.

## Scope

The top-level `backend/bpf` domain contains the one-shot `compile_bpf`
capability and immutable `BpfCompiledRealization`. It deterministically lowers
one continuous Plan interval into symbolic BPF/resource/parameter/Zone intent,
ordering constraints, and interval-end intended dynamic state.

## Owners and consumers

| Fact/capability | Owner | Compiler use / consumer |
|---|---|---|
| `Plan` | Simulator Planner | immutable semantic input |
| retained `TopologySlice` / Topology facts | Simulator Slicer | approved read-only deterministic lookup |
| `compile_bpf` | BPF backend | one-shot lowering only |
| `BpfCompiledRealization` | BPF backend | downstream immutable handoff and explicit next-call baseline |
| container software/files/commands/daemon | future functional Layer through SEED deployment | not installed or modified by BPF backend |
| PID/netns/ifindex/MAC/veth/bridge/BPF object/map/generation | future runtime owner | never compile-time bound here |

## Contract

Slice-0 compilation starts from the canonical empty dynamic-realization state.
A nonzero Plan start requires a previous compiled result with an exactly
matching end Slice/timestamp and topology/Zone-identity signature. There is no
hidden progression or implicit reconstruction from the current Slice.

Connection transitions produce symbolic per-direction attachment demands.
Initial full directional qdisc targets accompany establishment; later
ParameterTransitions retain their sparse changed fields while interval-end
state folds them onto the previous full target. Forwarding ADDED/CHANGED uses
the transition source and next hop to query the unique formal Connection and
derive direction; REMOVED and CHANGED recover old realization from explicit
previous intended state. No full path, FEC, MPLS label, live attachment, or
backend strategy is invented.

Every Zone has a deterministic backend-local unique `/32` identity allocated
by sorted Zone ID from `198.18.0.0/15`. It remains stable when the formal Zone
service satellite changes. This is packet-facing BPF identity, not a Simulator
Zone field or a future UT operational-address contract.

The result preserves each exact `ForwardingTransitionPrecedence` and adds only
mechanical technical constraints: resource before qdisc/use, qdisc before use,
every stop-use transition before resource retirement, and a Zone Difference's
forwarding state before service-edge activation/retirement. It is structured
intent, not a command list or runtime schedule.

Planner short-lifetime suppression is authoritative. Compiled active
Connections therefore need only be a consistent subset of formal boundary
Slice Connections, and omitted Connection/forwarding evolution is not
reconstructed from raw Slices. Zone service-edge changes follow folded planned
forwarding terminals when packet-facing route evidence exists; a raw Zone
assignment alone does not replace an existing edge without a planned
forwarding change.

## Invariants

- dependency direction is backend → public Simulator facts/contracts;
- no Simulator state mutation, Slicer advancement, Planner synchronization, or
  Model/RouteModel execution;
- compilation is deterministic for equal inputs and deeply immutable on
  success;
- baseline lineage, Plan/Slice provenance, unique connection resolution,
  planned-resource use, and interval-end consistency fail closed;
- intended end state is sufficient for deterministic consecutive compilation;
- only `compile_bpf` and `BpfCompiledRealization` are public BPF concepts.

## Failure semantics

Wrong public argument types raise `TypeError`. Missing/mismatched lineage,
contradictory Plan/Slice evidence, inactive forwarding dependencies, exhausted
Zone identity space, and inconsistent intended state raise `ValueError`. A
failure publishes no object and mutates no input.

## Prohibited behavior

No generic backend framework, Compiler owner inside Simulator, semantic route
recomputation, runtime binding, Runner/preparer/maintenance lifecycle, shell
command generation, container installation, v1 adapter/object migration, UT
prerequisite, or public Context/Resolver/Registry/Manager/Model/Bundle/Slot/
Generation/OperationGraph.

## Extension points

The private symbolic intent formats, qdisc mechanism, Zone allocator, and
technical constraint construction may evolve without creating a public
architecture concept. Runtime binding and functional-Layer deployment are
separate future approvals.

## Maintenance

Update `backend/bpf/README.md`, public imports, maintenance/object/import maps,
architecture gates, focused unit tests, canonical main-chain smoke, and
`CURRENT.md` together. Do not add an Inspection Tool as part of Compiler
maintenance.
