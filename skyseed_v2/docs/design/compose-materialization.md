# Bounded Compose Project Materialization

## Scope and placement

`ComposeProjectLauncher` is a small deployment/integration tool for bounded
materialization of one already-generated Docker Compose project. Its placement
is deliberately outside the Emulator domain model:

```text
Static scenario
    -> SkyseedDockerCompiler
    -> one generated Compose project
    -> ComposeProjectLauncher
    -> Docker containers
```

The Emulator constructs static entities and the scenario. The Compiler emits
the single Compose output. The launcher starts and verifies the complete
project in bounded sequential batches. It does not belong to Descriptor,
Catalog, Emulator, Simulator, dynamic topology/planning, or satellite-network
runtime semantics. It is consequently implemented under
`skyseed_v2.materialization` and is not shown in an Emulator domain class
diagram.

A future execution Runner may call this tool, wrap it, or replace it. This
slice does not choose that Runner design and does not introduce a dynamic
Runner.

## Input contract

The caller supplies:

- the path of an existing Compose file;
- one explicit Compose project name;
- a positive, configurable `batch_size`;
- an explicit partition of every declared service into `long_running` or
  `successful_exit_helpers`.

The launcher discovers the service inventory with `docker-compose -p
<project> -f <file> config --services`, rejects an empty or duplicate result,
and sorts the names before partitioning them. Thus batch membership is stable
and independent of the order returned by Compose. The caller's two state
classes must be disjoint and must exactly cover the declared inventory;
unknown and unclassified services fail closed.

Service classification is evidence owned by the caller, not a name heuristic
inside the generic launcher. The S4-scale example classifies the two generated
SEED image-cache services from their existing `dummies/` build Dockerfiles and
classifies the remaining scenario nodes and InternetMap as long-running. The
generic launcher does not know S4, node roles, or SEED naming conventions.

## Materialization and verification contract

The launcher computes the SHA-256 of the existing Compose file before startup.
For every sequential batch it:

1. verifies that the same file still has the same digest;
2. executes `docker-compose -p <project> -f <file> up -d <batch services>`;
3. stops immediately if that command fails;
4. checks all services completed so far for an expected state;
5. verifies the Compose digest again;
6. records the one-based batch index, exact service tuple, and elapsed time.

After the tail batch it performs one full-project state and inventory audit.
Every long-running service must be running; every successful-exit helper must
be exited with status zero. Missing, duplicate, extra, abnormally exited, or
still-unclassified project services are errors. A transient `created` state is
polled only within a fixed verification bound; it is not accepted as success.

Project scope is not inferred from global Docker state. State discovery uses
the same Compose project and file to obtain container IDs, then verifies the
exact Compose project/service labels returned by `docker inspect`. The
launcher never invokes the Compiler, writes Compose, produces another output,
edits a service, drops a declared service, changes batch size automatically,
or uses a second project.

## Fail-closed boundary

`ComposeMaterializationError` retains the failing batch index and exact batch
services when applicable. A failed middle batch prevents all later `up`
commands. Partial earlier success is never returned as full materialization
success. There is no automatic per-service retry, batch-size fallback,
alternate Compose file, or global cleanup.

Cleanup is a separate caller-owned operation and must remain bounded to the
named project and its exact generated image tags. A future execution layer may
define supervised retry and idempotent recovery; those contracts are not
pre-designed here.

## Why batching is retained

The 2026-07-17 S4-scale validation environment uses Docker Compose 1.27.4. An
unchanged 365-service project's one-shot `up -d` previously blocked in the
client after creating only 50 project containers. The same Compose file, same
project, and complete 365-service inventory then materialized successfully in
explicit batches of 16. The bounded-launcher revalidation started the same
full-scale scene directly in 23 batches without first attempting a monolithic
`up` and without a fallback path.

There is also historical v1 evidence for bounded startup. The v1 option
`--compose-satellite-batch-size` has default `12` in
`skyseed_v1/src/skyseed_v1/runtime/experiment.py` at the
`compose_satellite_batch_size` option declaration, and the accepted S4 command
recorded in `skyseed_v1/docs/migration/stage7_acceptance_ZH.md` did not override
that default. It is therefore reasonable to infer that the accepted historical
run used approximately 12 Satellite services per stage. This is supporting
operational evidence only; no v1 startup control flow or Runner architecture is
copied into v2.

These observations establish batching as a Compose materialization
optimization, not a reduced experiment and not a backup success path. They do
not prove that 12 or 16 is universally optimal. Batch size remains an explicit
configuration value; automatic tuning is outside this slice.

## Validation boundary

Contract tests use a fake command runner and cover deterministic ordering,
configurable and non-divisible batches, empty/duplicate/unknown inventories,
middle-batch stop behavior, one project/file throughout, accepted helper exit
zero, rejected long-running exit, missing/extra final services, and Compose
file drift. They also prove that no Compiler call or second Compose write is
part of the launcher.

The real validation used one generated Compose file with 365 services, a batch
size of 16, and a full final result of 363 running services plus two successful
exit helpers. Container, routing, ICMP, InternetMap, per-batch timing, and
bounded cleanup evidence is recorded in
`docs/evidence/s4-scale-batched-compose-materialization-validation.md`.
