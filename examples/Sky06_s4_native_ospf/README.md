# Native S4 with BIRD OSPF

[s4_native_ospf.py](s4_native_ospf.py) uses the stored S4 shell: **348 satellites in six
orbits, 355 GroundStations, an ordinary ground LAN, 732 default Zones and 600 seconds
at five-second cadence**. Satellites run BIRD Area 0 OSPF; GSs remain outside OSPF.
Automatic Traffic and Internet Map are enabled. For the shared pipeline and mode choices,
see [BPF/Linux navigation](../../docs/architecture/overview.md#choose-a-forwarding-mode).

## Configure the experiment

The entry defines `FORWARDING_BACKEND = "linux"` once for Emulator and
`simulator.create_default_simulator(..., forwarding_backend=FORWARDING_BACKEND,
route_model=EdgeRoutes())`. Controller/execution inherit the selection. The explicit
`EdgeRoutes` instance is this Example's policy; the ordinary Linux default is empty
business routes. Explicit partial or complete Models remain valid.

- [config/bird.conf.template](config/bird.conf.template) owns Area 0, point-to-point ISLs,
  ordinary 10/40 timers, stub identities, access-interface exclusions and GS redistribution.
  BIRD imports exact GS /32s with kernel protocol 253 and advertises changing Zone /32s.
  The explicit `bird-kernel-options.conf` include supplies the adapter's 4-MiB notification
  buffer. Recovery scans remain enabled; no daemon restarts accompany handovers.
- [config_helpers.py](config_helpers.py) reads the template once, shares GS prefix facts
  and expands endpoint-dependent access names per satellite. One
  `NativeRoutingEnvironment` uses the complete static node set, also used by Compiler.
  Node descriptions expose those same stable identities; no extra allocation occurs.
- [edge_routes.py](edge_routes.py) associates each Zone with its three geographically
  nearest GSs, without a distance cutoff. It installs satellite→attached-GS edges and
  GS→associated-Zone uplinks through the smallest attached satellite identity. Missing
  accesses withdraw uplinks; BIRD supplies satellite transit. Traffic independently
  selects fixed pairs from static identities; local arrangements do not prove end-to-end reachability.

SkySEED's `emulator.configure_native_software` validates concrete configurations and
uses ordinary SEED Node packages/files/startup. It adds no Routing Layer; Example authors
own protocol policy. FRR remains supported by the adapter and test fixtures, while this
teaching entry uses BIRD. See [software authoring](../../docs/api/assembly-and-control.md#native-routing-software)
and [node payloads](../../docs/user-guides/node-payloads.md). Regenerate after changing
identity-dependent configuration or the node set; preserve existing accepted output.

## Generate and inspect

Use the [required SEED/Python environment](../../docs/user-guides/getting-started.md)
and [Linux host prerequisites](../../docs/user-guides/linux-host.md), including
[native neighbor capacity](../../docs/user-guides/linux-host.md#native-ipv4-neighbor-capacity)
before deployment. Dynamic Map behavior requires the separately adapted local SEED
Map source; `--no-internet-map` omits that optional service.

From the repository root, in the calculation terminal:

```bash
conda activate skyseed_dev
python scripts/check_dev_environment.py
python examples/Sky06_s4_native_ospf/s4_native_ospf.py --output /absolute/path/native-s4 --deploy-parallel 4
```

Choose a fresh output path. Bare Python defaults to `serve`: it calculates the initial
range, publishes views and stays open for continuation. `IDLE` means calculation is
waiting, not runtime completion. Add `--operation generate` for finite calculation
and execution materials; `--duration-seconds` changes the initial range.
Generation does not deploy. To deliberately replace inactive output, first archive it
and finish owned cleanup, then use `--override`.

Open the selected output's `index.html`: PhysicalWorld, Zones, Planner, Compiler and
Traffic generation preview. The deterministic default selects up to two GS/Zone pairs
in both directions at five-second intervals. Missing active sources are recorded without
replacing a pair. The published view may lag while rendering.

## Deploy, prepare and run

In a second terminal with the same environment, change to the selected output:

```bash
cd /absolute/path/native-s4
./deploy.sh
./run.sh
```

Deployment reports image reuse, native Docker build/pull/start output and confirmed
startup. Runtime prepares B0, then calls the captured [readiness.py](readiness.py)
`prepare` function before Traffic and formal time. It waits for expected BIRD ISL
adjacencies to be Full and checks up to four actual GS–Zone routes with access/return
edges. Queries use at most four nodes concurrently. Missing routes keep waiting;
command/configuration errors fail. There is no arbitrary preparation deadline, and
this bounded initial sample is not a continuous all-pairs guarantee.

`runs/<invocation>/readiness/` holds command status, stdout/stderr, `criterion.json`
and per-round `native.jsonl`; `runner/prepared.json` describes B0. The runtime terminal
reports preparation results and `Runtime formal clock starting`. With retained materials,
`run.sh` keeps the last network active when materials end, waiting for continuation.
Pause holds applied state, while BIRD/application wall clocks continue. Reset drains the
invocation and prepares B0 again. [Captured preparation contract](../../docs/development/experiment-control.md#user-preparation-function).

From a third terminal in the output directory:

```bash
python experiment_control.py status
python experiment_control.py continue --seconds 300 --batch-seconds 300
python experiment_control.py pause
python experiment_control.py resume
```

Status prints a human summary on stderr before detailed JSON stdout: requested range,
active runtime frontier, available materials, preparation/pause/wait/errors and a next
action. Continuation needs the original calculation process; resume does not calculate.

## Observe the network

Open [Internet Map](http://localhost:8080) → **Map** while deployed. Use **Settings →
Drag Fixed** to keep a node where dragged. New outputs show **Stable node identity IPv4**
in node details (`Des`); that node /32 differs from migrating Zone service /32s.
In a node terminal, use `ip -4 address show dev sv2identity`, `ip -4 route`,
`birdc show ospf neighbors` or `birdc show route all` (GSs have no BIRD daemon).
A narrow on-demand filter such as `icmp and host <Zone-address>` observes those capture
points; clear Search to see packet activity in Filter mode. It is not a full transit path.

After runtime, **Traffic observation / replay** provides a globe, sender totals/flows
and raw records. Rotate/zoom, select a communication and seek/play recorded runtime time.
Sealed geometry supplies context; replies, failures and source migrations are recorded
events. Linux has no complete intermediate per-packet transit evidence. Aggregate totals
do not create packet events or hops. Map capture is not automatically Traffic replay
evidence; BPF can display its recorded ingress observations.

## Stop and clean up

```bash
python experiment_control.py cancel-runtime
# Original run.sh terminal: wait for runtime cleanup and subsequent Tool results/return.
python experiment_control.py cancel-computation
# Original calculation terminal: wait for session closure/return.
./cleanup.sh
```

A control reply only acknowledges the request. Watch the original execution terminal
for `Runtime resource cleanup succeeded` or failure, followed by separate observation
Tool results. A stop before preparation instead confirms no runtime resources were
acquired. Watch the calculation terminal for `Calculation session closed` after owned
computation and presentation drain. Missing endpoints or process exit alone prove no
cleanup. Finite generation/run return on their own; deployment still needs `cleanup.sh`.

Cleanup prints graceful-stop/removal counts and elapsed time, then `Deployment cleanup
succeeded` only after verification. Ordinary GS/shared services retain configured hooks
and graceful stopping. Inspect exact Compose project labels for containers/networks;
standalone Runner helpers use `org.skyseed.runner.foundation` and are not covered by
Compose-only queries. No image-cache pruning is required.

## Validation

The accepted normal output includes invocation `20260922T061416.080496065Z-2047246`:
the user completed the full 600-second workflow, cancellation/cleanup and an inter-satellite
Map ping. Preserve it; it predates later presentation/adapter corrections. Detailed source
conditions, FRR/BIRD campaigns, warnings, interruptions and raw evidence locations are in
[S4 experiments and acceptance](../../docs/history/native-s4-experiments.md). Current
capability and limits are in [CURRENT](../../docs/CURRENT.md); no fresh full-S4 result is
implied by focused implementation checks.
