# Docker Compose v2 Project Materialization

## Scope and placement

`ComposeProjectLauncher` is a small deployment/integration tool that
materializes one already-generated Docker Compose project. Its placement is
outside the Emulator domain model:

```text
Static scenario
    -> SkyseedDockerCompiler
    -> one generated Compose project
    -> ComposeProjectLauncher
    -> Docker Compose v2
    -> Docker containers
```

The Emulator constructs static entities and a scenario. The Compiler emits the
single Compose output. The launcher starts and verifies that complete output.
It does not belong to Descriptor, Catalog, Emulator, Simulator, dynamic
topology/planning, or satellite-network runtime semantics, and it is not shown
in an Emulator domain class diagram.

A future execution Runner may call, wrap, or replace this tool. This slice does
not decide that Runner contract and does not introduce a dynamic Runner.

## Input and command contract

The caller supplies:

- an existing Compose file;
- one explicit Compose project name;
- a positive `parallel_limit`, defaulting to 16;
- an explicit partition of every declared service into `long_running` or
  `successful_exit_helpers`;
- optionally, the explicit `batched` strategy and a positive `batch_size`.

The only supported CLI is Docker Compose v2 through `docker compose`. The
launcher first executes `docker compose version --short`, requires a parseable
major version of at least 2, and fails closed if the plugin is absent, the
command fails, or the version is unsupported. It never invokes the legacy
`docker-compose` executable and never falls back to it.

All project operations share this centralized prefix:

```text
docker compose --parallel <parallel_limit> -p <project> -f <compose-file>
```

The launcher discovers services from that exact project/file, rejects an empty
or duplicate result, and sorts names before any optional batching. The caller's
two state classes must be disjoint and exactly cover the declared inventory;
unknown and unclassified services fail closed. Classification is caller-owned
evidence, not a service-name heuristic inside the generic launcher. The scale
example identifies the two generated SEED image-cache helpers from their
existing `dummies/` build Dockerfiles and classifies all scenario nodes and
InternetMap as long-running.

## Default single-invocation strategy

The formal default is:

```text
one Docker Compose v2 full-project up
    + controlled Compose internal parallelism
    + one strict final inventory/state audit
```

After service discovery and expectation validation, the launcher computes the
Compose SHA-256 and executes exactly one:

```text
docker compose --parallel <N> -p <project> -f <file> up -d
```

No service subset is appended. A non-zero command result is returned as a
`ComposeMaterializationError`; it does not trigger batching, another parallel
limit, a second project, v1, or another Compose output. On success, the
launcher polls within a fixed verification bound and requires every expected
long-running service to be running and every helper to have exited zero. It
rejects missing, duplicate, extra, created-at-timeout, or abnormally exited
services. The Compose digest must remain unchanged.

The result records the Compose version, selected strategy and parallel limit,
Compose CLI elapsed time, and the time from the `up` call through final stable
verification. `parallel_limit` is configurable; 16 is a validated starting
value, not an automatically tuned or universally optimal value.

## Explicit batched tooling mode

`strategy=ComposeLaunchStrategy.BATCHED` retains the earlier sequential batch
helper for compatibility, diagnosis, and explicit use on special machines. It
uses the same file, project, version gate, `parallel_limit`, service
classification, digest checks, and strict final audit. `batch_size` is
configurable and service membership is deterministic, including a
non-divisible tail.

Each batch stops fail-closed on command or state failure and records its index,
exact services, Compose CLI time, and time through verification. No later batch
runs after a failure. This mode is never selected automatically and is not a
fallback from the default single strategy.

## State and project-scope verification

Project scope is not guessed from global Docker state. State discovery uses
the same Compose project/file to obtain container IDs, followed by
`docker inspect`. Every returned container must carry the exact project label
and one unique service label. A helper may be `created` or briefly `running`
while polling, but only `exited` with status zero is success. A long-running
service succeeds only in `running` state.

The launcher never calls the Compiler, writes Compose, produces another
output, edits a service, drops a declared service, retries a service, changes a
parallel/batch setting, or performs cleanup. Cleanup remains caller-owned and
must be bounded to the named project, its project-labelled networks, and exact
generated image tags.

This integration helper may later sit below `Runner.initialize()`. It is not a
top-level runtime architecture, a current Runner, or part of Simulator. This
slice does not add automatic recovery, missing-service reconciliation,
runtime restart policy, or dynamic batch tuning.

## Why the default changed and batching remains

The historical validation host used Docker Compose 1.27.4. Its one-shot
365-service `up -d` stopped making progress after creating 50 containers, even
with `COMPOSE_PARALLEL_LIMIT=8`. The same complete file/project subsequently
started through 23 explicit service batches. A later formal launcher run also
passed in 23 batches, but repeated a v1 CLI call and cumulative project-state
audit for every batch.

Docker Compose v2.40.2 instead completed the same 365-service project through
one `up -d` invocation with `--parallel 16`, followed by one final strict audit.
That establishes v2 single-project materialization as the supported default
and removes fixed batching from the normal plan. It does not prove that every
v2/Engine combination or parallel value will have the same performance.

The old batch evidence remains useful as an explicit diagnostic capability.
It is not a smaller experiment, Emulator design, dynamic Runner design, or
hidden success path.

## Docker 19.03 build compatibility boundary

On this validation host, Compose v2's default BuildKit path against Docker
Engine 19.03 produced derived images whose final `Config.Cmd`/`Entrypoint` was
empty. The seven-service smoke therefore failed closed before any scale run.
Rebuilding the same unchanged Compose file with `DOCKER_BUILDKIT=0` preserved
the existing Dockerfile startup commands and passed all runtime checks. The
formal scale build used that one explicit compatibility setting and verified
all 362 node image commands before `up`.

This is an environment-specific build compatibility requirement, not a second
Compiler path or a scenario change. It should be re-evaluated when the old
Engine is upgraded; the launcher itself neither builds images nor chooses a
builder.

## Validation boundary

Fake-subprocess contract tests cover Compose v2 command construction,
`--parallel`, missing/unsupported v2 fail-closed behavior, absence of v1
fallback, the default one-call strategy, explicit configurable batching,
stable ordering and tail batches, one project/file, command failure
propagation, helper/long-running states, missing/extra services, Compose drift,
and absence of Compiler or output creation.

The real seven-service smoke and formal 365-service result are recorded in
`docs/evidence/s4-scale-compose-v2-single-materialization-validation.md`.
The previous v1 batched run remains historical evidence in
`docs/evidence/s4-scale-batched-compose-materialization-validation.md`.
