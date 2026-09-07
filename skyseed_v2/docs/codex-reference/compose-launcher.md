# Compose launcher contract

## Authority

Normative detail under `../accepted-design.md`. Implementation state is
recorded only in [`CURRENT.md`](../CURRENT.md).

## Scope

Launch and verification of one already-generated Docker Compose v2 project.
This boundary does not compile, generate, edit, plan, or dynamically execute a
simulation.

## Owners and consumers

`skyseed_v2.compose.ComposeProjectLauncher` owns the bounded subprocess
lifecycle. Callers own the Compose file and create
`ComposeServiceExpectations`; the launcher returns immutable
`ComposeMaterializationResult` and `ComposeBatchResult` evidence. Integration
examples and tests consume those results.

## Contract

The launcher validates its file, project name, explicit launch strategy,
parallel/batch limits, service expectations, and verification policy. On
`materialize()` it:

1. verifies Docker Compose v2;
2. reads the declared service set and the Compose file SHA-256;
3. requires expectations to classify that exact set;
4. invokes either one full-project `up -d` or explicitly selected sequential
   batches;
5. verifies every long-running service is running and every helper exited
   successfully;
6. verifies the Compose digest before and after launch work;
7. returns immutable timing, service, strategy, version, and digest evidence.

`SINGLE` and `BATCHED` are caller-selected strategies, never an implicit
fallback sequence.

The canonical generated `start.sh` is a separate thin Compose control, not a
call to `ComposeProjectLauncher`. The optional launcher explicitly defaults to
`--parallel 16`; current generated scripts set neither `--parallel` nor
`COMPOSE_PARALLEL_LIMIT`. Their effective concurrency cannot be inferred from
the launcher default. Build-context grouping and historical host settings are
explained in the [deployment decision record](../provenance/optimization-decision-history.md#d2).

## Invariants

- one launcher instance addresses one resolved Compose file/project;
- every declared service has exactly one expected final-state category;
- command execution uses `docker compose`, not legacy `docker-compose`;
- the Compose file digest is stable for the operation;
- batch evidence is ordered and covers the selected services exactly;
- success means complete final-state verification, not merely command exit 0.

## Failure semantics

Invalid constructor values raise `TypeError` or `ValueError`.
Command failure, malformed Compose output, service-set mismatch, digest
change, timeout, missing container, wrong final state, or nonzero helper exit
raises `ComposeMaterializationError` with bounded message context and, when
applicable, `batch_index` and `services`. No failed operation returns a result
or attempts another strategy.

## Prohibited behavior

No Docker/Compose compilation, file mutation, service-name guessing,
unclassified service, legacy executable fallback, automatic batching
fallback, teardown/recovery orchestration, Simulator mutation, Tool reading,
Planner, backend compiler/preparer, or simulation Runner role.

## Extension points

Callers may supply the documented command runner, clock, sleeper, verification
limits, executable name, and explicit strategy for testing or deployment
integration. A new strategy or result/failure field is a public contract
change.

## Maintenance

Update `compose/launcher.py`, `skyseed_v2.compose` exports, Compose class
details and local README, launcher/integration tests, examples, public-import
mapping, and wheel checks together.
