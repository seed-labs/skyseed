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

Human lifecycle feedback uses the existing best-effort `_console` and is promptly flushed
to stderr. The control client prints its human summary before detailed JSON-only stdout,
using existing owner snapshots (calculation state/range, runtime frontier/available range,
preparation, pause, material wait, cancellation and errors); it introduces no status record
or new lifecycle owner. Calculation start/range publication, Runner preparation/formal-clock
start and pause/material-wait transitions report actual milestones. Waiting messages occur
on state transitions, never every poll. Retained range readiness means waiting, not exit;
finite materials still finish normally. A successful cancel reply acknowledges the request and directs
the user to the original owner terminal. Missing endpoints and lost replies do not
establish completed cleanup. The retained calculation terminal reports session
closure only after computation and accepted presentation have drained, identifying
primary errors separately.

The runtime terminal reports resource cleanup starting, then success only after
delivery, Traffic, Runner and connection-observer cleanup have returned successfully.
Generated `run.sh` reports subsequent observation Tool generation separately,
including each failed Tool. Tool failures do not change the runtime exit status.
The deployment CLI used by `cleanup.sh` reports its own cleanup start and result;
runtime cleanup does not remove independently deployed containers. Console-write
failures cannot replace an owner's result or primary error.

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

## User preparation function

`ExperimentController(preparation=module_level_function, preparation_inputs=...)`
captures the function's source with ordinary input provenance and detaches its
JSON-compatible inputs into `inputs/preparation.json`. `PreparationContext` supplies
initial prepared runtime, Planner and compiler facts without a Simulator owner.
The captured function is invoked in a separate process through the **same**
initial-readiness command lifecycle below (`python -m skyseed_v2.experiment.preparation`).
There is no second orchestration loop, protocol registry or convergence-report contract.
It accepts one context argument and may simply sleep. Normal return releases the
handoff; exceptions become the primary command failure with the traceback retained
in stderr. No arbitrary preparation deadline is imposed.

Module-level source and `input_files` are the portability contract: list local
imports/data, use captured context paths, keep generation under a main guard, and
pass dynamic values through `preparation_inputs`. Capture preserves the function’s
package context and directory hierarchy, so imports such as `from .helper import VALUE`
resolve listed dependencies from the snapshot even after the originals change. List
`__init__.py` files when their code is required; omitted initializers act as namespace
packages within the captured tree, without falling back to the original package.
Standalone-file functions retain sibling import support. Python environment dependencies
remain external. A function and a raw command cannot both be selected. Pause/resume
and later topology updates do not rerun the hook; a fresh reset invocation does.
See [authoring/API examples](../api/assembly-and-control.md#user-preparation-function).

## Initial readiness command

`ExperimentController(initial_readiness_command: Sequence[str] | None = None)`
optionally inserts one command after successful `Runner.prepare()`/B0 and before
Traffic start, successor delivery, `Runner.run()` and formal timing. The default
adds no wait or query. Runner remains protocol agnostic; the existing generated
execution owner starts, observes and drains this command under its runtime claim.

Supply argv, never shell text. Execution uses the output directory as cwd and appends
`--output <absolute-output> --invocation-directory <absolute-invocation>`. Include a
Python command's source in `input_files` (or as `entry_path`), and use its resolved
absolute filename as an argv element. Generation maps exact captured source names
from ordinary `inputs/provenance.json` to `inputs/sources/...`; it does not rewrite
substrings, infer imports or copy dependencies. The selected generated Python
interpreter executes the captured script. Later edits to the original source do
not change that output; regenerate to apply them.

Execution definition v6 records optional `initial_readiness_command` and the
generation interpreter. Existing v5 output is still accepted without a hook.
Before launch, execution must write `runs/<invocation>/runner/prepared.json`, using
the existing `runner-inspection-handoff` v1 schema with the actual B0 snapshot,
frontier and runtime bindings. A write failure stops execution and still cleans up.
The author command owns the meaning of readiness and may consume this file.
It must cooperate with SIGTERM and drain its query children before exiting.

`readiness/command.json` records exact argv/cwd, UTC start/end, exit status,
separate elapsed seconds, errors and stdout/stderr paths. Runtime `status` exposes
`initial_readiness`; existing runtime events record its start/end. Performance
records `experiment/initial-readiness` separately; the existing Runner lifetime
rollup includes this wait. The wait consumes wall time without advancing simulated
time. A zero exit releases execution; a nonzero exit remains the primary failure,
with later cleanup failures attached.

Cancel/reset/Ctrl-C stop and drain the owned command process group before Runner
cleanup. Admission and completion both recheck cancellation. There is no forced
kill or wall-clock termination deadline. Reset cancellation is normal control flow:
after drainage/cleanup the existing loop constructs a new Runner, activates a fresh
B0, and repeats readiness. A genuine command failure is not converted into a reset.
Commands that launch isolated query sessions must own their cooperative drainage;
the native Example closes query stdin to stop and reap in-container commands.

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
