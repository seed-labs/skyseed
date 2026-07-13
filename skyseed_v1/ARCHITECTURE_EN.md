# Skyseed v1 Architecture

## Product boundary

Skyseed is a library and experiment platform, not one fixed S4 runner. It
follows the useful SEED Emulator separation between a logical description and
a platform compiler, then adds an explicit online runtime for time-varying
satellite state.

```text
scenario/example
  -> SkyseedEmulator + selected layers/services
  -> logical topology IR
  -> planner IR
  -> backend compiler plan
  -> runtime scheduling and activation
  -> concrete backend execution
  -> observations and audits
```

The Satellite Internet 1.0 S4 profile is the first validated example of this
platform. It is not the definition of the platform.

## Source responsibilities

Directories are created when real code is migrated into them; this document
defines their intended ownership in advance.

- `core/`
  - stable interfaces, intermediate representations, lifecycle vocabulary,
    registry primitives, and shared errors
  - must not import BPF, Docker, Linux host, SEED, or scenario code
- `layers/`
  - optional whole-emulation capabilities such as constellation, connectivity,
    routing, Internet access, dynamics, and observation
- `services/`
  - capabilities installed on selected nodes, such as traffic, measurement, or
    control services
- `models/`
  - replaceable orbit, visibility, zone, ground, and link policies
- `topology/`
  - creates topology slices and stable `TopologySnapshot` IR
- `planning/`
  - computes routes, G/Z FEC intent, temporal events, parameters, and mutation
    intent without executing host commands
- `compilers/`
  - pure translation from semantic IR into a backend-specific plan; compilation
    must not mutate the host
- `runtime/`
  - backend-neutral transaction order, horizon preparation, activation,
    retirement, worker coordination, and rollback orchestration
- `backends/`
  - concrete BPF, Linux, qdisc, namespace, and Compose mechanisms
- `adapters/seedemu/`
  - mapping between Skyseed logical objects and SEED Emulator objects,
    registries, bindings, layers, and compilers
- `observability/`
  - records raw events, metrics, timing, artifacts, and runtime state
- `audit/`
  - evaluates topology, planning, runtime, backend, and equivalence claims from
    plans and observations
- `operations/`
  - preflight, host readiness, cleanup, and session lifecycle commands
- `visualization/`
  - export formats and presentation-specific assets
- `cli/`
  - argument parsing and process exit codes only; it may select and invoke a
    use case but must not implement topology, planning, backend, or audit logic

## Stable IR boundary

The first stable intermediate representations are the behavior-compatible
forms already present in Satellite Internet 1.0:

- `TopologySnapshot`
- `RuntimeUpdatePlan`
- `NetworkMutationPlan`
- temporal `RuntimeEpoch` and `ParameterEpoch`
- backend compilation/preparation plans
- execution, rollback, observation, and audit reports

Migration may move and split their implementation, but must not silently
rename fields, weaken validation, change serialization, or alter fingerprints.
A schema change is a separate semantic change and is outside the structural
refactor unless explicitly approved.

## Dependency direction

Dependencies point from policy and orchestration toward stable abstractions,
never from the core toward a concrete backend.

```text
core
  <- models
  <- topology
  <- planning
  <- compilers

core <- runtime -> backend interfaces
core <- backends
core <- adapters

observability <- runtime/backends
audit <- core IR + observations
cli/examples -> public interfaces only
```

Additional rules:

1. planners do not invoke Docker, `tc`, `bpftool`, or namespace commands;
2. compilers produce data and command intent but do not apply it;
3. runtime decides when state changes, not why a planner chose that state;
4. backends do not choose routes, gateways, zones, or horizon semantics;
5. audits do not repair or fallback around failures;
6. examples own experiment choices; CLI defaults must not hide a second
   scenario definition;
7. only one route is canonical after cutover.
8. `experiment.py` is the sole editable example definition; effective profiles
   are resolver output written into the run directory, never a second source.

## Runtime invariants preserved from 1.0

- network activation is the highest-priority runtime path; console progress,
  observation, audit projection, and artifact presentation must never apply
  backpressure to an epoch update. They must be bounded, deferred, coalesced,
  or allowed to lose presentation freshness before they delay the network;
- strict `bpf_owned` has no Linux G/Z, MPLS, LFIB, NAT, or Mini-Internet
  forwarding fallback;
- EventTimeline remains the runtime update authority;
- one run has one continuous frozen physical-link timeline; rolling horizons
  consume aligned frame offsets from it and must not restart physical-link
  selection independently;
- every prepared horizon must have the exact node and slot materialization
  identity compiled into the initial Compose topology; drift fails before
  Compose build and runtime interface creation is forbidden;
- future state is prepared inactive and activated only at its semantic boundary;
- partial or mismatched worker results are not ready;
- qdisc/link lifecycle remains Linux-owned unless a later gate changes it;
- frozen replay inputs remain pinned and mismatches fail closed;
- rollback and postflight must leave zero owned residue.

## Examples, tests, and output

- `examples/` contains user-facing executable composition directories. Each
  example owns a short `run.py`, one editable `experiment.py`, README, ignored
  `output/`, and only example-specific fixtures. The canonical S4 experiment lives in
  `examples/s4_operational_clean/`.
- `tests/` contains developer verification grouped into unit, contract,
  integration, replay, and system tiers.
- `data/` contains reusable committed catalogs, not runtime output.
- each example's ignored `output/` contains its generated artifacts; deleting
  that directory after cleanup resets that example's local generated state.
- durable conclusions from real runs belong in `docs/history/`, not in source
  modules or test fixtures.

## Directory growth rule

A stable concept that can reasonably acquire implementation, configuration,
fixtures, generated-artifact descriptions, variants, or its own README gets a
self-contained directory instead of joining a large flat file list. That
directory must state its public boundary and ownership. Nesting is not added
for a single tiny file. Generated state may live only in the owning example's
ignored `output/`, never as editable source or an implicit input path.

Apply this rule across v1, especially to examples, backend families, runtime
maintenance, audit families, and tools. It is a continuing organization rule,
not permission to perform another bulk move without contract and import gates.
The current crowding inventory and staged split candidates are recorded in
`docs/architecture/directory_growth_EN.md`.

## Deferred design directions

Ideas that have been discussed but are not yet accepted architecture are kept
in `docs/architecture/design_backlog_EN.md`. Recording an idea there preserves its
motivation across human/AI handoffs; it does not make the idea a release
commitment, correctness invariant, or expensive-run gate.
