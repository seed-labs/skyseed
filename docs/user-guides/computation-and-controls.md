# Generate, continue, deploy and control

All seven maintained examples default to foreground retained computation. They compute only the initial
finite request, publish results and optional views, and wait efficiently for another request.
They do not deploy, execute or calculate an unbounded future by default.
Startup explains this retained lifetime. The calculation terminal returns after
`cancel-computation` drains owned work and presentation; `--operation generate` selects
finite publication and return. A separate `run.sh` with retained materials keeps the final
network active until continuation or runtime cancellation, even when calculation is `IDLE`.

## Initial range and finite cutoffs

```bash
# Default retained S4; five-second cadence is declared in the entry.
python examples/Sky02_s4_default/s4.py --duration-seconds 600 --batch-seconds 300

# One-shot calculation at any cutoff; these examples exit after publication.
python examples/Sky00_tiny_network/tiny_network.py --operation generate --through physical-world --output /tmp/tiny-physical
python examples/Sky01_s5_custom_models/s5.py --operation generate --through planner --duration-seconds 30 --output /tmp/s5-plans
python examples/Sky00_tiny_network/tiny_network.py --operation generate --through compiler --output /tmp/tiny-materials

# Retention also works at partial cutoffs.
python examples/Sky00_tiny_network/tiny_network.py --through planner --output /tmp/tiny-retained
```

PhysicalWorld produces positions/candidates. Planner additionally publishes Plans. Compiler
additionally creates execution materials and deployment controls. Partial cutoffs do not
prepare deployment or execute downstream stages. `--no-tools` disables optional rendering;
`--no-traffic` disables traffic generation; `--no-traffic-telemetry` independently disables BPF packet/path collection. Neither option changes Model results.

Observation cadence, `PlannerConfiguration.frames_per_batch`, and delivery duration are
independent. A delivery can flush a partial Plan while retaining operational state. Example
Python constants `DEFAULT_OPERATION` and `DEFAULT_BATCH_DURATION` define lifecycle defaults;
initial duration is a Timeline setting. CLI options override those choices.

## Direct Python configuration

```python
from datetime import timedelta
from pathlib import Path
from skyseed.experiment import ExperimentController
import tools

# network and simulation come from your explicit scene/Simulator assembly.
controller = ExperimentController(network, simulation, Path("output"),
    entry_path=Path(__file__), through="planner", traffic_enabled=False)

def show_completed(output, publication):
    return tools.write_experiment_tools(output,
        physical_world=simulation.get_physical_world(), publication=publication)

controller.serve(delivery_duration=timedelta(seconds=300), on_completed=show_completed)
# For a one-shot script, use controller.generate() instead, then optionally render.
```

While `serve()` owns its foreground thread, another caller may use
`controller.continue_calculation(additional_duration=timedelta(seconds=300))`, or
`end_time=<aware datetime>`, with an optional new `delivery_duration`. The generated command
client is usually simpler from a second terminal. Its editable defaults are ordinary Python
values: `ADDITIONAL_SECONDS`, `TARGET_END` and `BATCH_SECONDS`.

## Continue from a second terminal

```bash
cd examples/Sky02_s4_default/output
python experiment_control.py status
python experiment_control.py continue --seconds 300 --batch-seconds 300
# Alternative target form:
python experiment_control.py continue --until 2026-01-01T00:20:00+00:00
```

After 0–600 seconds at five-second cadence, adding 300 computes 605–900. It reuses the
original scene, Models, Planner memory and Compiler predecessor. Targets must increase and
align to the original cadence. Status separates initial/requested/completed endpoints,
material batches, computation waiting and presentation. Refresh the browser after a completed
request; the view may lag while rendering and identifies its recorded data. A terminated
calculation process cannot be recovered by `continue`.
The control client prints a concise human summary on stderr before detailed JSON on stdout.
It compares runtime's active frontier with the requested range and available materials,
identifies preparation/pause/wait/errors and suggests the relevant next action. Endpoint
unavailability does not prove that an owner cleaned up successfully.

## Deployment and execution

For Compiler output only:

Deployment reports image reuse, native Docker build/pull/start output and confirmed service
startup. Cleanup reports counts and elapsed time for graceful stops and removals. Long stops
retain configured hooks/signals/grace periods; request acceptance is not completion.

```bash
./deploy.sh                      # Deployer builds and starts Compose
./run.sh                         # Runner plus explicitly enabled Traffic; separate foreground
python experiment_control.py pause
python experiment_control.py resume
python experiment_control.py reset
python experiment_control.py status
```

Run controls from another terminal while `run.sh` is active. Pause holds network updates after
a complete boundary; container apps, wall clocks and in-flight traffic continue. Resume releases
that pause and does not request calculation. Reset drains Traffic/Runner/evidence and starts
a fresh invocation over retained materials without recalculating Models or rebuilding Compose.
A retained Runner with exhausted materials waits for a successor.

Explicit automation remains available through `--operation run` and
`run_generated_experiment(output, cleanup=True)`. The former retains calculation and manages
execution; the latter operates existing generated controls. `cleanup=False` assigns deployment
teardown to its caller. Neither is an example default.

## Exit and cleanup

```bash
python experiment_control.py cancel-computation   # calculation only
python experiment_control.py cancel-runtime       # Runner/Traffic only
# Or request both owners:
python experiment_control.py shutdown
# Wait for run.sh to finish its cleanup, then for independently deployed output:
./cleanup.sh
```

Ctrl-C follows the foreground operation's owned drain. Cancellation requests are not proof
that cleanup has finished. The control terminal acknowledges accepted requests while
keeping its JSON response on stdout. Watch the original `run.sh` terminal for
`Runtime resource cleanup succeeded`, then the separate observation Tool result.
A stop before preparation instead confirms that no runtime resources were acquired.
Watch the original calculation terminal for `Calculation session closed`; it waits
for owned calculation and presentation work. Any failure message needs inspection;
a disappeared endpoint or terminal exit alone does not confirm cleanup.
`cleanup.sh` prints `Deployment cleanup starting` and, only after successful removal,
`Deployment cleanup succeeded` (or a failure). Independently deployed containers remain Deployer-owned until
`cleanup.sh`; managed execution performs that teardown after runtime drainage. Repeated Ctrl-C
cannot abandon accepted cleanup. No elapsed-time cutoff forces a process kill.

A user preparation function can wait or configure the initial prepared network
before Traffic and formal time begin, including when `run.sh` is invoked later.
See the [captured function and context example](../api/assembly-and-control.md#user-preparation-function).

After owner closure, inspect shared `outcome` and cancellation results as well as
the terminals. A failed runtime/computation drain produces a failed result; missing
outcomes remain indeterminate. The separately owned Etcd service stays available
for those queries until [explicit stop](#stop-restart-or-use-external-etcd).

## Interaction setup and independent programs

Feedback uses one Etcd namespace per generated experiment. Host programs, container
programs and the two owner control consumers use the same public client/API. User
values have no built-in fault meaning; only an explicit control requests framework
work. The [API map](../api/assembly-and-control.md#independent-feedback-clients) locates
methods; the [developer reference](../development/experiment-control.md) owns precise
record, scheduling and failure contracts.

Use `skyseed_dev` with the project dependencies installed as in [getting started](getting-started.md).
From the repository root, install the separate service executable once:

```bash
python scripts/setup_interaction.py
```

This installs pinned Etcd/etcdctl 3.6.5 for Linux x86_64 or aarch64, verified against
the release checksums, at `~/.local/share/skyseed/etcd/`. It starts no daemon.
Service startup searches an explicit Python `executable` argument, then
`SKYSEED_ETCD_EXECUTABLE`, then `etcd` on PATH, then that user-local directory.
`--prefix /chosen/directory` changes installation location; set the environment
variable to `/chosen/directory/etcd` if it is not on PATH. No global daemon is needed.

Controller construction is inert. Generation prepares the output, starts its member
(including for partial cutoffs), and records `.control/interaction.json`. The service
survives finite generation, runtime exit and retained calculation closure. The host
endpoint is loopback HTTP; local container access uses Etcd's own Unix listener.
The file records experiment identity, endpoints and ownership, not a Model snapshot.
`InteractionClient.from_output(path)` reads it without starting any service;
`SKYSEED_INTERACTION_ENDPOINT`, when set, overrides the discovered client endpoint.
Leave that override unset for ordinary local use. `close()` closes a client's
connection, not Etcd or any experiment owner.

### Read focused framework facts

Run this Python in the activated host environment, with the generated output as cwd.
State reads require only the retained service; on-demand queries also require a live
`run.sh` owner. This example assumes Runner has completed preparation:

```python
from skyseed.experiment import InteractionClient

client = InteractionClient.from_output(".")
try:
    print(client.state("computation"))
    print(client.state("runtime"))
    reply = client.query("materials", offset=0, limit=10, timeout=10)
    print(reply["status"], reply.get("result"))
    print(client.query("bindings", offset=0, limit=10, timeout=10))
finally:
    client.close()
```

`query()` returns a request result envelope: inspect `status` before using `result`.
Page bindings/materials with `offset` and `limit` (1–100); materials report selected
manifest/version/range and `runner.frozen_through`. `-1` means initial preparation
has not started. Before Runner construction the progress can be null.
For a chosen communication, call `query("communication", ground_station_id=gs_id,
zone_id=zone_id, network_scope=scope, timeout=10)` using a canonical GS node-ID string
and Zone/scope from your scene or generated `results/traffic/workload.json`.
The answer reports ACTIVE identities/addresses and service facts, not reachability
or measured packet hops. `query("inspect", timeout=10)` returns a relative file path
under `.control/inspections/` and its invocation ID; open the file on the output host.

### Publish custom data; let the Model choose when to read it

This independent host write requires the service but no live computation or Runner.
Use the output directory as cwd:

```python
from skyseed.experiment import InteractionClient

client = InteractionClient.from_output(".")
try:
    row = client.user_put("research/link-parameters", {"delay_us": 2000})
    print(row["value"], row["version"])
    client.user_put("research/link-parameters", {"delay_us": 3000},
                    expected_version=row["version"])
finally:
    client.close()
```

A version conflict raises `ValueError`; reread and decide whether to retry. Nothing
in this write triggers a fault interpretation, recalculation, cache invalidation or
revision. The [short ParameterModel example](model-extension.md#read-user-data-in-a-model)
reads this key inside its ordinary Model method and chooses its effect. A subsequent
`continue` computes new time with that Model; `revise` explicitly asks it to recalculate
eligible existing windows. Reads can happen whenever the author chooses.
`user_read([keys...])` optionally reads multiple keys at one Etcd revision;
`watch_user(key, start_revision=..., timeout=...)` waits for one change. Neither adds
a Model hook. Authors own cache validity and handling unavailable external data.

### Connect a container program

After Compiler generation, Compose supplies these to each experiment node:

| Generated item | What it supplies; what the author supplies |
|---|---|
| `SKYSEED_INTERACTION_ENDPOINT`, `SKYSEED_EXPERIMENT_ID` | Endpoint and namespace for `from_environment()` |
| `inputs/interaction-client/`, mounted read-only at `/opt/skyseed-interaction` | Standalone `skyseed_interaction.py`, requirements and usage README; the full framework is not needed in the container |
| `/run/skyseed-interaction` mount for the default owned service | Same member's native Unix listener, usable with isolated network namespaces and no new network route |
| Your node program/image | Python 3.10+, installed client requirements, import path, authored program, and its startup command |

Install `etcd3gw>=2.7,<3` and `requests-unixsocket>=0.4,<0.5` when building your
chosen image; the same declarations are in the generated `requirements.txt`.
Bind mounts appear when the container runs, so that mounted requirements path is
not automatically available during an image build. The framework does not install
Python or start your program for you. Use ordinary [node payloads](node-payloads.md).

Inside that configured container, this complete snippet imports the mounted client:

```python
import sys
sys.path.insert(0, "/opt/skyseed-interaction")
from skyseed_interaction import InteractionClient

client = InteractionClient.from_environment()
try:
    print(client.state("runtime"))
    print(client.user_put("node/report", {"observed": True}))
finally:
    client.close()
```

The host can read `client.user_get("node/report")`; container code may also use
`submit`/`result` exactly as below. Default mounts assume Docker is on the output
host and the program can access the socket directory (ordinary SEED root programs
can). Non-root images must arrange appropriate access; remote Docker needs its own
explicit deployment/mount and endpoint arrangement. Authored programs are not
generated by the client provisioning step.

### Submit explicit controls and look up their effects

Use a second activated host terminal in the output. Each command below saves one
receipt; JSON is stdout and human diagnostics are stderr. Retain the receipt until
the effect is resolved:

```bash
python experiment_control.py pause > pause-receipt.json
python experiment_control.py result "$(cat pause-receipt.json)"
# Repeat result lookup until terminal, then release the pause:
python experiment_control.py resume > resume-receipt.json
python experiment_control.py result "$(cat resume-receipt.json)"
# A live retained calculation owner is required for either calculation command:
python experiment_control.py continue --seconds 300 > continue-receipt.json
python experiment_control.py result "$(cat continue-receipt.json)"
python experiment_control.py revise > revision-receipt.json
python experiment_control.py result "$(cat revision-receipt.json)"
```

These are separate actions to choose as needed. Check each terminal status before
assuming success. A successful CLI exit means the submission or lookup worked;
it does not assert that the request's effect succeeded. `shutdown` returns a map of
owner names to receipts; look up each receipt separately. Controller convenience
methods also return receipts, not updated state. For Python automation, with a live
prepared runtime and output cwd:

```python
import json
from pathlib import Path
from skyseed.experiment import InteractionClient

client = InteractionClient.from_output(".")
try:
    receipt = client.submit("runtime", "pause")
    Path("pause-receipt.json").write_text(json.dumps(receipt.artifact()))
    try:
        result = client.wait(receipt, timeout=10)
    except TimeoutError:
        print("Completion indeterminate; retain the receipt and query again.")
    else:
        print(result["status"], result)
    print(client.state("runtime"))
finally:
    client.close()
```

Later, `client.result(json.loads(Path("pause-receipt.json").read_text()))` works
even after owner closure while the service remains reachable. `wait` timeout does
not cancel the request. For scheduled controls use an aware scene timestamp, e.g.
`pause --at 2026-01-01T00:07:03+00:00`; this example is useful only in a range that
reaches that point. Pause aligns upward to cadence and holds after that boundary.
Other eligible controls can pass a distant scheduled request. Supported `parameters`
values and their effective boundaries are in the [scheduling contract](../development/experiment-control.md#scheduling-and-parameter-effect).

### Revision selection and subsequent observation

Revision requires retained Compiler computation and a compatible saved predecessor.
It recalculates the complete dependent suffix, not an isolated delivery with
incompatible descendants. `revise --at <aware-scene-time>` chooses the first complete
window starting at/after that target; it submits immediately rather than waiting
for the simulated clock. Initial delivery zero is replaceable before preparation,
including an initial range shorter than the delivery duration.

Runner does **not wait for a revision** by default. Registration does not freeze
materials, but entering PREPARING does; READY and ACTIVE windows are also frozen.
Pause holds activation and may still allow preparation. If the candidate loses that
race, Producer restores the predecessor that will execute and recalculates a later
eligible suffix. The result retains deferral attempts or explicitly rejects when
none remains. A prior materials query is an observation, not a reservation.

Inspect the request's `requested_position`, `scheduled_position`, `actual_position`,
`history` and revision `result`/`progress`. Revision results include attempts and,
after adoption, `adopted` and `selection` (version, first index, candidate root and
catalog path). Query materials again for current selection/freeze position.
`results/compiler/selection.json` records the selected chain; Runner load/activation
records under `runs/<invocation>/` establish actual use. Selection alone does not.
Completed publications pin selected evidence, and the optional views refresh only
after publication. See [output and evidence locations](outputs-and-tools.md).

Framework checkpoints restore Planner and Compiler continuation. Model instances,
their private state and user values stay live; they are not rolled back. A Model may
therefore be called again at an earlier time. Confirmed adoption retains a compatible
continuation even if later evidence cleanup/reporting fails (`FAILED` with
`adopted=true`). Before an adoption attempt, local cancellation can drain a revision
waiting for Etcd. After a possibly accepted attempt, it must resolve selection
before restoring an old calculation head. The [revision contract](../development/experiment-control.md#revision-and-reusable-continuation)
defines these boundaries.

To observe meaningful changes, with output cwd and the service running:

```python
from skyseed.experiment import InteractionClient

client = InteractionClient.from_output(".")
try:
    changes = client.state_history("runtime", after_revision=0, limit=100)
    for change in changes:
        print(change["session_id"], change["published_at"], change["version"])
    # Continue paging after changes[-1]["version"] if the page is full.
    # After catching up, watch from the last processed version + 1.
finally:
    client.close()
```

`watch_state("runtime", start_revision=last_version + 1, timeout=10)` returns one
meaningful change, excluding heartbeats. A timeout only means no change was observed.
`state()` separates observation time/version from current heartbeat freshness;
independent owner records are not a simultaneous process snapshot.

### Read completion and failure honestly

| Observation | Meaning and next step |
|---|---|
| Receipt / `SUBMITTED`, `RUNNING`, `DEFERRED` | Stored or in progress; inspect the result again |
| `SUCCEEDED` | The stated command effect completed; for cancellation this requires known owner closure/outcome, not just admission |
| `REJECTED`, `CANCELLED`, `EXPIRED` | Inspect the reason; no successful requested effect is implied |
| `FAILED` | Inspect final state, errors and any `adopted=true`; failure after an effect does not undo it |
| `live=false` | Explicit owner closure, possibly with a failed `outcome`; inspect `primary_error`, runtime `traffic`/`cleanup`, computation `execution_error`/`presentation`, and top-level `error` |
| `live=true` but `available=false`, `stale=true` | Last observation is stale; closure and cleanup remain unproven |
| `outcome=null`, unavailable store, lost reply or wait timeout | Completion remains indeterminate; retain receipt/IDs and query after reconnection |

The final lifecycle handling preserves cleanup/handoff errors in shared outcomes
and marks cancellation `FAILED` when drainage failed. Lost acknowledgements retain
terminal decisions for republication without replaying completed effects. Runtime
outcomes exclude independently owned deployment cleanup and later Tool rendering.
Use `cleanup.sh` and its own records for deployment, and the Tool result for views.
`controller.status()` remains a local diagnostic, without the shared freshness contract.

### Stop, restart or use external Etcd

After both owners close, inspect their results and finish independently owned
deployment cleanup. From the output directory:

```bash
python -m skyseed.experiment.interaction_service stop .
# Optional later records-only restart for queries:
python -m skyseed.experiment.interaction_service start .
python experiment_control.py status
# Finish online inspection:
python -m skyseed.experiment.interaction_service stop .
```

Stop excludes active owner claims and refuses even stale `live=true` records.
Investigate those owners instead of treating disappeared processes as proof of
drainage. Explicit closure with a failed outcome allows service stop, but stopping
Etcd neither certifies nor repairs that failed cleanup. Stop preserves the database,
logs and evidence. Restart after explicit stop preserves experiment identity and
records; it restores no Models, control consumers, Runner, containers or kernel
state. It is not `continue` or `reset`. A crashed member without an explicit stopped
record is not automatically taken over by `start`; investigate its recorded process
and logs. The current service helper provides no crash-recovery override.

For an existing external service, configure it before generation. With your normal
`network`, `simulation` and authored `entry_path` already assembled, choose endpoints
actually reachable from the output host and your containers:

```python
from pathlib import Path
from skyseed.experiment import ExperimentController

controller = ExperimentController(
    network, simulation, Path("output-external"), entry_path=entry_path,
    interaction_endpoint="http://127.0.0.1:2379",
    interaction_container_endpoint="http://etcd.research.example:2379",
)
```

Those addresses are placeholders for your deployment, not a service provisioned by
SkySEED. Supply/manage Etcd v3 with its HTTP gateway, endpoint reachability, namespace
access and persistence. Omitting `interaction_container_endpoint` omits container
client provisioning. An isolated `network_mode: none` container cannot reach a TCP
service without an author-supplied reachable arrangement. The client has no exposed
username/password, token or client-certificate configuration; authenticated external
deployments needing those options are outside this helper's current contract. This
is a trusted experiment namespace, not per-program access isolation. SkySEED neither
starts nor stops the external service or sets its retention. Its stop command only
marks this output's attachment stopped after owner closure.

Owned Etcd uses one-hour MVCC compaction. Current values, request histories and
immutable meaningful state-history records persist without automatic expiry; old
versions of overwritten user values do not have a separate archive. On
`InteractionHistoryCompacted`, reread current values or page retained state history,
then resume the watch explicitly. Compaction is not a bound on total database size.
During outages `InteractionUnavailable` is raised; new ownership/replacement decisions
wait or fail, while valid admitted work and local cleanup can continue. Durable
observations may remain stale. Bulk inputs, sealed material versions, completed
publications, runtime evidence and visualizations remain [file-backed](outputs-and-tools.md#files-records-and-authored-programs).
