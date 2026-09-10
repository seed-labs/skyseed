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

Generated controls remain separate from the optional public
`ComposeProjectLauncher`, whose default is `--parallel 16`. Starlink Compiler
controls now call private deployment support in this same module: shared
services first, then generated Shell identities in order. The shared private
policy keeps startup defaults at 64 services / eight concurrent requests at every
resident count, with explicit environment configurability; batch size and request
concurrency are distinct. The resident threshold affects cleanup only. Dependencies are checked before
admission; a derived batch file contains only admitted services and `--no-deps`
prevents implicit later-Shell startup. The original Compose file is preserved; namespace dependencies in a batch use
verified immutable container IDs.
Expected one-shot helpers may complete successfully. Mini and static teaching
controls retain their existing deployment paths.

Deployment records lightweight `/proc` checkpoints at batch/group boundaries in
`runtime/deployment-measurements.jsonl`. Per-service memory extrapolation,
predicted free-memory gates, pressure pause/resume and automatic pressure abort
have been retired. Batches, dependency order, identity checks, bounded query
retries, ordinary command errors and scoped cleanup remain. There is no outer
runtime-lag or arbitrary total-wall-time kill policy. Main's fixed origin,
sequential late-boundary execution and successor READY deadline are unchanged.

During generation, the private build-plan writer in the
same package groups exact node context content, paths and modes. Each group
retains one representative build and gives its services the same explicit
project-scoped content image; per-node container settings, mounts and identities
remain individual. The generated startup validates each unique image once,
checks project labels on generated images, requires an image ID for external
images, verifies the exact Compose image set, and builds only the base
prerequisite and representative contexts when required. It creates no per-node
image aliases. Failed container inventory or stale images with owned containers
stop startup. This packaging work is separate from the public launcher's
already-compiled-project contract. Build-context grouping and historical host settings are
explained in the [deployment decision record](../provenance/optimization-decision-history.md#d4).

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

Generated disposable-network cleanup, shutdown overrides, immutable-ID hooks and
the four environment controls are specified in the
[Compose owner README](../../src/skyseed_v2/compose/README.md). Cleanup speeds up
from eight to 16 actual IDs as remaining residency falls below 2000. Full-scale
qualification of these new settings is pending; old deployment/teardown timings
retain their original settings and manual-acceleration disposition.
