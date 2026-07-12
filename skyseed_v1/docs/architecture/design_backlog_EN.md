# Design Backlog

This is the durable notebook for design directions that were discussed but
were not implemented or accepted as current architecture. It exists so useful
ideas survive conversation and handoff without being mistaken for product
behavior.

An entry here is **not** an implementation commitment, release blocker,
expensive-run gate, or permission for a broad refactor. Before work starts, its
assumptions must be checked against the then-current code and measurements.

## How to maintain this file

- Add an entry when a discussion produces a concrete direction, tradeoff, or
  unresolved question and the work is intentionally deferred.
- Record why the idea matters, what should be preserved, and what remains
  undecided. Do not preserve only a proposed filename or mechanism.
- Use one of these states: `explore`, `candidate`, `ready`, `implemented`, or
  `retired`. Most new discussion belongs in `explore` or `candidate`.
- Moving an entry to `ready` requires an explicit decision, a bounded scope,
  and proportionate verification—not merely repeated agreement in discussion.
- When implemented, link the resulting architecture decision, guide, tests, or
  release evidence. Historical detail may then be shortened, but the original
  motivation should remain discoverable.
- Nothing becomes a correctness gate merely because it is listed here. A gate
  must protect a current invariant or plausible failure surface and belong in
  the relevant test or operations policy.

## D-001 — Turn `frozen` into an explicit compilation boundary

**State:** candidate

The valuable core of the current frozen workflow is the separation between
logical description/planning and concrete execution. A runtime should consume
a schema-versioned, immutable plan rather than silently recompute routes or
backend actions at an epoch boundary. For an unbounded satellite network this
becomes rolling compilation: one horizon runs, the successor is compiled and
prepared inactive, and the runtime only activates it at the semantic boundary.

Preserve:

- deterministic temporal and backend plans;
- input identity, schema, integrity hashes, and resource-reference coverage;
- inactive preparation followed by boundary activation;
- runtime refusal to silently replan or select a fallback route;
- artifacts usable by execution, visualization, and audit.

Refine later:

- distinguish physical-link sampling, ground qualification, compiled update
  plans, host binding caches, and historical replay instead of calling all of
  them `frozen`;
- replace product-facing `freeze`/`thaw` vocabulary with concepts such as
  `compile_run_plan`, `seal_build_manifest`, `load_compiled_plan`, and
  `activate_prepared_generation` where those names fit the real contracts;
- separate a portable semantic/compiled plan from host-local paths, container
  PIDs, interface identities, diagnostic evidence, and cleanup observations;
- decide whether loading a prior prepared bundle remains a developer replay
  tool rather than a public alternative run route;
- move historical producer/consumer self-proof into focused tests and audits,
  retaining only cheap load-time checks that protect current execution;
- measure horizon planning, backend compilation, serialization, hashing,
  externalization, and validation separately. The historical “frozen horizon
  preparation” duration must not be treated as if it were all integrity-check
  overhead.

This is an architectural refinement after v1 closure, not authorization to
remove the current successful preparation route.

## D-002 — Grow Skyseed into a composable emulator platform

**State:** explore

Skyseed is intended for other researchers to construct their own satellite
Internet, experiment platform, and algorithms—not only to run the committed S4
and S5 scenarios. Preserve the SEED-inspired separation between logical network
description and concrete backend materialization.

Questions for a future design slice:

- public `SkyseedEmulator` or equivalent composition object;
- installable, optional constellation, connectivity, routing, dynamics,
  Internet-access, observation, and application/service layers;
- stable model and planner extension interfaces for user algorithms;
- how Skyseed layers map into current SEED Emulator objects and compilers while
  keeping SEED behind a versioned adapter boundary;
- which intermediate representations are public compatibility contracts;
- how a user selects Docker/Linux/BPF or future backends without backend policy
  leaking into the logical description.

The examples should remain understandable composition roots and demonstrations
of public APIs, not become the place where platform internals are implemented.

## D-003 — Generalize ground presets and duration qualification

**State:** candidate

The committed S4 and S5 ground presets are bounded evidence for their declared
start time and qualification window. The two-hour S5 analysis is a useful
coverage sample, not proof of Earth-relative periodicity: satellites orbit and
Earth rotates, so connectivity does not repeat as a simple closed loop.

Future work may provide:

- a documented GW/UT/zone preset builder using a declared shell, epoch,
  duration, cadence, visibility policy, and survival/coverage criteria;
- provenance and qualification bounds embedded in each generated preset;
- deliberate policies for reducing GW/UT cardinality while retaining useful
  geography and connectivity;
- requalification of S4 and S5 under one explicit methodology;
- clear behavior for changed start dates, longer finite runs, and eventually
  indefinite operation;
- rolling qualification or an online ground-policy model when a finite preset
  is no longer sufficient.

Do not weaken the current duration gate merely to allow a longer command. A new
duration model must replace the invariant explicitly.

## D-004 — Build the long-term test strategy around module contracts

**State:** candidate

Cheap, stable tests should be the normal acceptance mechanism for localized
changes. Tests belong with the behavior contract they protect and should move
or be rewritten alongside a module boundary rather than being copied wholesale
after a refactor.

Desired shape:

- unit tests for pure models, planners, compilers, and small runtime state
  machines;
- contract tests for serialized IR, backend plans, identity, ownership, and
  failure semantics;
- integration tests for composition, SEED compilation, worker handoff, and
  bounded backend interactions;
- replay tests only where ordered producer/consumer behavior cannot be covered
  more cheaply;
- system and long runs for cross-module behavior, host/kernel integration,
  scale, resource tails, and failures that emerge only over time.

Historical experiments are evidence and possible fixture sources, not
automatically permanent gates. Tests should remain directly runnable by a
human, while automation may use more parameterized interfaces.

## D-005 — Complete the external visualization integration

**State:** explore

The v1 visualization interchange location and exporters already exist. A
separately developed visualization module is expected to integrate with
Skyseed later.

Before integration, define and version the handoff for:

- orbit/satellite identity and synchronized timestamps;
- ground station, UT, GW, and zone identity;
- physical-link samples and planned/runtime slice updates;
- experiment/run identity and artifact discovery beneath example-owned output;
- live versus offline consumption and acceptable observation lag;
- compatibility behavior when either project evolves independently.

Visualization must consume authoritative plans or observations and must not
become another topology planner or a source of runtime backpressure.

## D-006 — Treat observability cost as measurable engineering cost

**State:** candidate

Console progress, durable evidence, timing, and audit have real value and may
have nonzero cost. The requirement is not literal zero overhead; it is that the
cost remains visible, attributable, bounded where appropriate, and does not
quietly become a dominant part of network updates.

Future performance work should:

- attribute planning, compilation, backend application, observation,
  serialization, and audit time separately;
- detect duplicate global scans, repeated large-object serialization, lock
  contention, and work performed after a timing boundary but charged elsewhere;
- compare scaling across satellite, ground-node, candidate-link, and epoch
  counts rather than relying only on a wall-clock threshold;
- retain coalescing or asynchronous observation when useful, but allow simpler
  synchronous mechanisms when measurement shows their cost is negligible;
- treat timing anomalies as investigation signals, not automatic process-kill
  deadlines while useful progress continues.

Shell IDs are not a scale ordering. The committed presets are S1=1584,
S2=1584, S3=720, S4=348, and S5=172 satellites. Cost qualification must use a
complexity vector—satellites, planes, candidate cross-plane slots, ground
resources, runtime slots, epochs, and backend materialization—not the numeric
shell suffix. In particular, an S5 result cannot be extrapolated as evidence
that S3 will fit or start safely; S3 needs an offline topology/capacity/cost
preview and a qualified ground preset before any real run.

## D-007 — Split oversized modules in bounded slices

**State:** candidate

The current crowding inventory and candidate package boundaries are maintained
in `directory_growth_EN.md`. The first likely target is
`runtime/experiment.py`, especially lifecycle orchestration, compiled-plan
production/consumption, progress, timing, artifact projection, and cleanup.

Each extraction should preserve one public route and lock down the affected
imports, serialized artifacts, failure labels, and call sequence with focused
tests. This is gradual design work, not another whole-tree structural move.

## D-008 — Add user extension and customization guides

**State:** explore

Examples now provide one editable `experiment.py`, a short `run.py`, and local
output ownership. Future guides should explain the next level of customization
without teaching users to edit platform internals:

- changing horizon count, duration, sampling interval, and start time;
- selecting a shell and understanding why satellite count derives from it;
- selecting or generating qualified GW/UT/zone inputs;
- installing optional layers/services and supplying a custom planner;
- choosing a backend and understanding environment requirements;
- locating compiled artifacts, observations, visualization exports, and
  cleanup evidence.

The guide should identify which edits are inexpensive configuration changes
and which require preset regeneration, backend qualification, or a system run.

## D-009 — Provide stage-aware operator progress

**State:** candidate

The durable runtime snapshot already records phase age, child processes,
container counts and samples, resource state, and epoch counters. The console
currently emphasizes phase transitions, so a long unchanged phase can look
opaque even while useful work continues.

Prefer a small stage-aware projection before building a general progress UI:

- emit a low-frequency unchanged-phase heartbeat from the existing durable
  snapshot, without adding another Docker scan or runtime participant;
- show phase age and the active child operation for stages without a reliable
  denominator;
- during Compose build, say explicitly that images—not containers—are being
  created and point to the planned Compose/container inventory;
- during Compose up/readiness, show created, running, ready, and required
  counts plus a small sample of container names;
- during preparation and runtime, show horizon/epoch completed versus total
  only where the producer exposes an authoritative total;
- during cleanup, show remaining owned-resource counts where already measured;
- provide an on-demand human `status` action that reads the same snapshot and
  prints useful container inspection/exec hints without mutating the run.

Do not display a percentage or progress bar for work whose denominator is not
known. Live Compose build logs may later be teed into a run-owned artifact, but
that requires bounded I/O and failure-report compatibility; it is not necessary
for the first improvement.

Implemented first slice: all unchanged phases now receive a bounded console
heartbeat projected from the existing durable snapshot. It includes phase age,
the active child operation, existing Compose container counts/samples, and an
explicit image-build explanation. Compose startup transitions also expose the
real stage ID, batch position/total, stage type, service count, target-container
sample, completion wall time, and current created/running counts. Broader
authoritative totals, an on-demand `status` action, and optional build-log
artifacts remain candidates.

Heartbeat child commands are projected as stable semantic activities (for
example `horizon_preview` or `compose_image_build`) rather than truncated raw
argument strings. Zero-container state is suppressed outside Compose phases;
once containers exist it remains visible during later phases.

Related entry UX implemented: direct example `preflight` now prints the
existing short human summary by default, while `preflight --format json`
retains the complete machine/audit report.

Direct example `clean` now reuses the scoped cleanup implementation, refuses
to act while an owned run is active, and proves readiness with a post-clean
preflight. Example startup also strips the one known retired SEED `PYTHONPATH`
override automatically, so this migration detail is no longer an operator
ritual.

## D-010 — Add a one-command reboot/demo readiness check

**State:** candidate

The current direct `preflight` is intentionally lightweight and `clean` adds
scoped residue inspection. A future human-facing `doctor` or equivalent should
summarize reboot-sensitive prerequisites without starting a network:

- Docker active/enabled and Compose identity;
- selected Python, Skyseed, SEED, kernel, and explicit bpftool identities;
- noninteractive privilege as a persistent NOPASSWD capability;
- bpffs mounted as filesystem type `bpf`;
- bridge-netfilter state or proof that host prepare can apply it;
- disk/memory headroom, active-run absence, and scoped residue;
- a short preview of the selected example.

It should distinguish a missing but safely preparable host prerequisite from a
code/configuration blocker, and it must not silently perform kernel, package,
Docker, or dependency upgrades. Until implemented, the post-reboot sequence is
documented in `../operations/environment_and_running_EN.md`.
