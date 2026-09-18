# Compiler materials and Runner boundaries

Changes to the calculation-to-execution handoff depend on material continuity and
clear runtime ownership. This reference locates those obligations. The [execution architecture](../architecture/execution.md)
explains how these mechanisms cooperate.

## Compiler and sealed materials

`compile_bpf(plan, physical_world, previous_result)` validates scene/lineage/cadence and
lowers one semantic replay into `BpfCompilationResult`. It never reruns a Model. Results
retain exact activation timestamps, output planned state, backend intentions, constraints
and predecessor indexes. Empty transitions still create boundaries.

Existing activation frontiers, Runner phases and atomic forwarding generations discharge
precedences where sufficient; otherwise exact pairwise constraints remain. The material
writer owns normalization, capacity accounting, codecs and seals. Its compact summary is
published without rescanning or round-tripping all output. History's compiler projection is
not a replacement for the authoritative binary execution payload.

Material format remains v4. A terminal artifact seal completes a finite delivery;
`delivery.final` identifies the last delivery in a finite chain. Retained sessions publish
nonfinal deliveries. The delivery record carries index, predecessor manifest hash,
end/Plan/state/generation and cadence. Nodes are fixed; only an append-only contiguous suffix
is admitted. No replacement of an already loaded future is supported.

## Runner resource lifecycle

Sealing a delivery makes input immutable; it does not make the corresponding network active.
One successor can be admitted/prepared while Execution retains the last complete ACTIVE state.


Constructing Runner acquires no network. `prepare()` opens the initial materials, validates
supported format/integrity/capacity, discovers deployment incarnations, acquires helper,
namespace sockets/maps/programs, constructs and prewrites the complete first delivery,
and activates boundary zero. `run()` starts the logical clock and advances from boundary one.
Execution creates current resources, establishes readiness, publishes forwarding generation,
then withdraws obsolete resources and joins deletion before exposing formal ACTIVE.
Future interfaces do not exist merely because future forwarding was prewritten.

One successor may be pending, preparing or READY. Maintenance loads, constructs and prewrites
it; only Execution activates it. Reservations derive from the first batch and fixed
`batch_capacity_factor` (1–16, default 2), using typed per-node peaks to allow peer work migration.
The extra reservation has a memory cost; exceeding it fails admission. Preparation starts
according to `batch_lead_fraction` in (0,1], default 0.75. Neither setting promises readiness
by a wall-clock target. Missing or late READY holds the last complete ACTIVE state and shifts
the execution clock when released. Material waiting is independent of user pause.

Maintenance reclaims retired batches/immutable node versions only after actual activation,
reader accounting and retained references permit it. Monotonic generation identities avoid
ABA reuse. Execution uses retained privileged sockets/syscalls rather than per-action Docker
or shell commands. Cleanup drains owned mutations and evidence and releases resources;
uncertain ownership is not silently discarded. Slow preparation/cleanup is not timed out.

## Traffic and observed facts

Traffic plans are explicit optional workloads built from Plans and compiled reachability.
Traffic's active tool owns probe processes and telemetry; Runner owns network state and time.
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
