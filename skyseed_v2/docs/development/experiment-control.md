# Experiment publication and control interfaces

Request publication and shutdown are ownership boundaries. The details below
support review of retained computation and independent runtime controls. User commands and terminal examples belong in the
[control guide](../user-guides/computation-and-controls.md).

## Calculation lifetime

Controller construction is inert. `generate()` owns finite generation and returns its output
path. `serve(delivery_duration=..., run=False, cleanup=True, on_completed=None)` retains the
same Producer until explicit cancellation. Both support `physical-world`, `planner` and
`compiler`; cutoff is fixed for the session. Partial cutoffs do not advance downstream
stages or prepare deployment. `run=True` requires Compiler and explicitly manages deployment,
Runner/Traffic and eventual cleanup through `run_generated_experiment`.

Initial Timeline, latest requested endpoint, computed frontiers, sealed compiler deliveries,
completed requests, waiting and termination are separate facts. The original definition
remains unchanged. `continue_calculation(additional_duration=... | end_time=...,
delivery_duration=...)` requests an aligned increasing suffix. The Producer alone extends
its cursor and retains Models, Planner memory and compiler predecessor. Finishing a request
flushes publications without shutting those owners down. Working memory retains necessary
current state and bounded queues, not every completed Plan/frame. There is no restart recovery,
mid-session promotion, background daemon, automatic future calculation or distributed execution.

Presentation has its own completion signal and drains independently before `serve()`
releases ownership.

## Completed range boundary

`results/experiment/completed.json` atomically publishes one completed request. It contains
the selected cutoff, completed endpoint, request/status facts, stream byte/count/hash summaries,
candidate prefix metadata, performance byte bound and compiler delivery count. The history
manifest (v4) marks `terminal=false` while retained; it does not falsely report process exit.
`generation-status.json` separately reports final termination, cancellation or failure.

Histories are append-only, grouped under their responsible result directories. Writers retain
incremental digests/counts and close gzip members when publishing a complete range. Readers
consume only the explicitly published byte prefixes. They never consume a suffix still being
written. Tools pin one completion record across all component readers and detached workers;
older pinned records remain readable after later append. Hash verification occurs in the
consuming pass. No full-history copy, replay or validation occurs on each internal batch.

Producer's `produce(..., completed=...)` reports a drained range to Controller. `on_completed`
is optional presentation side work with `(output_path, publication)` arguments. Controller
runs it outside calculation and coalesces pending refreshes to the newest complete range.
Slow or failed presentation cannot prevent continuation or Runner progress. Accepted side
work drains at shutdown; failures are recorded separately. `tools.write_experiment_tools`
accepts the publication or reads the latest completed record and never reruns Models.
Standalone Performance and network export also pin that same request boundary. Export
uses recorded frame counts and timestamps, not the coverage of an earlier rendered page.
Runtime views additionally wait for the invocation's completed `runs/latest.json` record;
an active run's partially written handoff and performance files are not presentation inputs.

Compiler deliveries have their own earlier readiness boundary: sealed immutable batch paths
are appended to `results/compiler/materials.jsonl`. Runner can consume them while the remainder
of the requested range is being calculated. Presentation waits for the coarser request boundary.

## Output ownership

| Location | Owner and content |
|---|---|
| `inputs/` | Experiment-captured sources, provenance, effective configuration |
| `results/physical-world/` | Formal positions and optional candidate evidence |
| `results/planner/` | Published Plan/evidence stream |
| `results/compiler/` | Compact compiler facts, authoritative execution materials, delivery references and execution definition |
| `results/traffic/` | Traffic probe input plans |
| `results/experiment/` | Completed range, history/status and generation performance/timing |
| `results/tools/` | Presentation invocation records |
| `deployment/` | SEED build/Compose files and Deployer-owned state/measurements |
| `runs/<invocation>/` | Actual Runner, Traffic and performance records; reset creates a new invocation |
| `visualizations/` | Browser pages and their relative data/assets |
| `.control/` | Internal staging and control events |

Root `README.md`, `index.html`, `experiment_control.py`, and (Compiler only)
`deploy.sh`, `run.sh`, `cleanup.sh` are the user entry points. Authoritative materials have
one location. Old `start.sh`/`launch.sh` and old-layout aliases are absent.

## Commands and cancellation

| Action | Meaning and owner |
|---|---|
| continue | Original computation owner calculates more; it does not resume Runner |
| pause / resume | Runner holds/releases future updates at a completed boundary; applications and clocks keep running |
| reset | Stop Traffic, drain Runner/evidence, then replay retained materials with a fresh Runner on the existing deployment |
| cancel-computation | Stop calculation admission and drain accepted work; independent runtime remains owned separately |
| cancel-runtime | Stop Traffic/Runner; retained calculation remains available |
| shutdown | Request both owners; managed execution retains the request through deployment/runtime handoff and cleanup |
| cleanup.sh | Deployer removes its deployment after runtime has released ownership |

Output claims protect replacement and deployment teardown while allowing calculation and
execution to coexist. Runtime exposes its control endpoint before claiming and until after
releasing ownership. Shutdown and that transfer are serialized: pre-handoff cancellation
prevents Runner construction, post-handoff cancellation reaches its owner. Request acceptance
is distinct from completed cleanup. `cleanup=False` keeps teardown with the caller.
No arbitrary wall-clock deadline, retry-count termination or forced kill substitutes for
actual completion. Primary errors retain any secondary cleanup errors.

At the physical cutoff, scoped query cooperation reads the same injected Models’
potential-pair and optional search/prediction declarations independently of Planner.
It performs no connection selection, planning advancement or Compiler work. This
preserves configured query scope without silently introducing all-pairs defaults.
