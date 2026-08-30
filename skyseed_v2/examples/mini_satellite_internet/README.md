# Mini Satellite Internet Reference

This small but reasonably rich reference is separate from the minimal
`complete_static_emulator` canonical example. It is a reusable satellite
Internet scenario rather than a Starlink replica or Planner-specific fixture:
one YAML-built Emulator owns the Skyseed constellation, static
GroundStation/IX attachment, ordinary SEED network and routing/eBGP layers;
that exact Emulator is both compiled into a static Docker/Compose project and
projected into the accepted A–F Slice / Difference chain.

The explicit scale is one AS, one Shell, four Orbits, eight Satellites per
Orbit, three GroundStations, five Zones and 64 real Slices plus synthetic
Slice 0. `scenario.yaml` also owns IX 110, ordinary AS 65110,
`service-router`, `service-net`, three GroundStation IX attachments and one
explicit eBGP peering. `zones.yaml` owns the static Zone layout. `simu.py`
explicitly records:

- Timeline `2026-01-01T00:00:30Z` through `00:11:00Z`, sampled every
  `10 s` at 64 real instants;
- maximum ISL distance `6,000,000 m`;
- GroundStation minimum elevation `5°`, maximum two access Satellites and
  maximum distance `3,000,000 m`;
- Zone representative-point minimum elevation `10°`;
- ISL/access bandwidth `1,000,000,000 / 500,000,000 bps`;
- ISL/access loss `100 / 200 ppm`;
- propagation speed `299,792,458 m/s`;
- Planner planning/publication windows, dwell, parameter, forwarding,
  coordination, and consecutive Plan progression policy.

The three GroundStation coordinates are retained in this v2 YAML as canonical
reference locations from the historical v1 catalog
`skyseed_v1/data/ground/starlink_gateways.json`, specifically the Porto
Alegre, Marsala and Yamaguchi records selected by
`s4_visualization_gateway_binding_12.json` and read by the offline
`examples/reference/V1StaticInputAdapter`. The v1 repository provides names,
city labels, latitude, longitude and zero-metre reference altitude, but does
not document an external source URL. The neutral `Reference Ground Station`
display names and coordinates do not claim to represent any commercial
operator's current in-service facilities. The example copies the values rather
than reading v1 at runtime and gives GroundStations no static Zone membership.

`north-america-east` (priority 10) contains the smaller
`northeast-priority` rectangle (priority 20). Their positive-area overlap is
latitude `38°..46°`, longitude `-80°..-68°`; a probe at `(42°, -74°)` has two
physical memberships and resolves authoritatively to `northeast-priority`.
Both representative points remain inside their own rectangles.

## Generate and run the canonical experiment

Run the formal comprehensive entry from the repository root:

```bash
python3 -m examples.mini_satellite_internet.main
```

Or directly from this directory:

```bash
python3 main.py
```

The command only generates one experiment. It snapshots the authoritative
scenario inputs, compiles the deployment, reconstructs Simulator/Planner facts
for the five generation-time inspection views, and publishes the runtime
controls after those views pass. It does not start containers or run
`BpfRunner`. The standard
upstream InternetMap image is enabled; pass `--no-internet-map` only to isolate
a suspected Map failure during diagnosis.

The normal controls are:

```bash
./output/start.sh
./output/run.sh
./output/cleanup.sh
```

`start.sh` starts the generated Compose deployment only. `run.sh` expects that
deployment to be running and directly invokes the private root
`skyseed_v2.experiment.execution` application composition, which verifies
`runtime/execution.json`, compact source-scope fingerprints, and generated-input
fingerprints before reconstructing Emulator →
Simulator/Slicer → Planner → BPF Compiler lazily from `output/inputs/` behind
the Runner's future-work supplier. These hashes are deterministic
source/generated-output consistency checks for accidental definition drift,
not security credentials. It runs the automatic public Runner path:
`prepare()` → `run()` → `cleanup()`. Runner cleanup removes Runner-owned BPF,
link, address, pin, and helper state; it does not stop Compose. Use
`cleanup.sh` to remove the containers and networks. After Runner cleanup,
`run.sh` invokes the existing Tool-owned command entry points to write post-run
static inspection bundles under `output/inspection/runner/` and
`output/inspection/traffic/`. Controlled interruption still writes an
`interrupted` bundle before returning exit 130; ordinary/cleanup failures are
recorded when finalization can run without masking the primary runtime error.

For the same start-then-run sequence in one command, use:

```bash
./output/launch.sh
```

If Runner fails, `launch.sh` deliberately leaves the deployment running for
inspection and does not invoke `cleanup.sh`. The automatic run follows nominal
Mini time with the Runner's 1:1 relative monotonic schedule and spans ten
minutes thirty seconds, plus Preparation and cleanup. Public `activate_next()` manual
stepping remains available for focused tests and debugging, but it is not the
canonical user workflow.

The `10 s` Slice interval is Simulator observation resolution. The Mini's
example-local `300 s` simulation-generation lookahead controls how far the
runtime supplier advances Slicer/Planner facts on each pull. It is distinct
from Runner preparation: the example-local `60 s` nominal-time lookahead and
explicit future depth of `8` boundaries are separate rolling preparation
bounds; the
current default tc-BPF implementation consequently proves a private maximum of
`9` resident generations. None of these values defines a permanent Runner
cadence, default depth, or Tool threshold. Planner midpoint transitions can
produce `5 s` nominal boundaries, and Runner must keep pace with the actual
semantic transition schedule. Simulation/Slice resolution, Planner publication
cadence, Plan nominal transition timing, Runner preparation depth, and Runner
execution cadence are independent.

Advanced users may run an equivalent `docker compose` command manually with
the generation's project name and `output/runtime/docker-compose.yml`, then
invoke `run.sh`.

The output is one user-facing scenario root with a runnable static environment
and isolated read-only inspection bundles:

```text
output/
├── inputs/
│   ├── scenario.yaml
│   ├── traffic-workload.json
│   └── zones.yaml
├── start.sh
├── run.sh
├── launch.sh
├── cleanup.sh
├── domain-history/
│   ├── slice-history.jsonl.gz
│   ├── planning-history.jsonl.gz
│   └── compiled-history.jsonl.gz
├── runtime/
│   ├── compose-project-name
│   ├── execution.json
│   ├── docker-compose.yml
│   └── per-node build contexts
└── inspection/
    ├── topology/
    │   ├── index.html
    │   ├── topology.json
    │   └── summary.txt
    ├── zone/
    │   ├── index.html
    │   ├── zones.json
    │   └── summary.txt
    ├── slice/                 # generation-time Tool bundle from main.py
    │   ├── index.html
    │   ├── slice-manifest.json
    │   ├── chunks/slice-*.js
    │   ├── summary.txt
    │   ├── inventory.json
    │   └── checksums.sha256
    ├── planner/               # generation-time Tool bundle from main.py
    │   ├── index.html
    │   ├── planner-manifest.json
    │   ├── planner-manifest.js
    │   ├── chunks/*.js       # lazy Plan/Difference/catalog/data chunks
    │   ├── summary.txt
    │   ├── inventory.json
    │   └── checksums.sha256
    ├── observation/           # generation-time Slicer/Planner timing evidence
    │   ├── index.html
    │   ├── execution-observations.jsonl
    │   ├── observation-records.js
    │   ├── observation-summary.json
    │   └── checksums.sha256
    ├── bpf-compiler/          # generation-time Tool reads precompiled results
    │   ├── index.html
    │   ├── manifest.json
    │   ├── manifest.js
    │   ├── chunks/*.js       # paged exact Plan/intent/baseline/diff evidence
    │   ├── summary.txt
    │   ├── inventory.json
    │   └── checksums.sha256
    ├── runner/                # runtime evidence + post-run Tool bundle
    │   ├── index.html
    │   ├── runner-invocation.json
    │   ├── runner-inspection.json
    │   ├── runner-events.jsonl
    │   ├── summary.txt
    │   ├── inventory.json
    │   └── checksums.sha256
    └── traffic/               # runtime tc-BPF evidence + post-run replay Tool
        ├── traffic-telemetry.jsonl
        ├── index.html
        ├── traffic-replay.json
        ├── summary.txt
        ├── inventory.json
        └── checksums.sha256
```

`main.py` first snapshots `scenario.yaml`, `zones.yaml`, and a supported
relative `ground_stations_file` when present. This generation itself builds
from those snapshots. It constructs the Emulator exactly once, renders and
compiles it, then calls the same `simu.py` progression builder named in
`execution.json` to project `SimulatorStaticInput`, create the formal
Simulator/Topology/Slicer, and prepare consecutive immutable Plans.
Those already-created facts and decisions feed the five generation-time
read-only Tools. `main.py` uses its small private continuous compile loop to
build the BPF realization chain for the Compiler inspection view. It does not
retain full historical domain objects for those Tools. Instead, the canonical
producer appends compact gzip JSONL evidence under `output/domain-history/`
before its owners release working facts. The precomputed Tools and later live
execution reconstruct their exact public inputs from those durable histories;
Tool directory replacement therefore cannot delete the canonical source.
Generic output loading, consistency verification, supplier creation, and
Runner lifecycle are owned by that private root application composition; it
starts with no real Slices or Plans, advances the Slicer with the configured
simulation-generation lookahead, synchronizes one next Plan, and compiles one
realization per pull.

`output/runtime/` is the compiled static container network and
`output/inspection/` contains only read-only views. Simulator Position,
Connection, Zone service, Route, and Difference results do not update live
containers by themselves. Dynamic preparation and activation begin only when
`run.sh` attaches `BpfRunner` to the matching live Compose project.
`main.py` does not create a placeholder Runner view. After each cleanup attempt,
the generated-execution caller explicitly invokes
`runner.inspection_snapshot()`, writes the detached handoff, and then `run.sh`
invokes the post-run Runner Tool to read that handoff plus durable event history.
That page is lifecycle/control-plane inspection, not fresh kernel/dataplane
inspection or active packet validation. Traffic telemetry is a separate
Runner-owned streaming artifact at
`output/inspection/traffic/traffic-telemetry.jsonl`. After cleanup, the Traffic
Tool combines that same-run stream with the generated Slice / Difference and
BPF Compiler inspection artifacts to write the standalone post-run
`output/inspection/traffic/index.html`. It validates exact generation/network
identity and presents unambiguous ICMP request/reply pairs as Ping transactions.
The example-local `inputs/traffic-workload.json` describes seven ordinary Linux
Ping series across GroundStation/Zone lifecycle events: 739 planned
`[start,end)`
slots with pre-runtime estimates of 534 paired transactions, 205 slots without
tc-BPF observation, and 4430 packet events. These estimates are not measured
runtime evidence. This workload is acceptance/reference orchestration only:
normal `run.sh` neither imports `traffic_workload.py` nor starts any Ping
generator, and neither the workload source nor `traffic-workload.json` is part
of normal generated-execution consistency validation. When the workload is
invoked separately for canonical validation, it validates its own schedule and
live Runner context;
GroundStation-to-Zone series keep one process across routing
and service changes; each Zone-to-GroundStation generator remains continuous
for one compiled service-satellite segment and changes only on service change,
removal, or logical-series completion. Supplier-frontier refills do not split
that process. Replay discovers observed flows and Pings from telemetry, keeps
the planned S1–S7 workload only as optional comparison evidence, and uses one
simulation timeline with selected-Ping request/reply replay controls. Position,
network, and Zone context remain globally shared;
it is neither part of Runner Inspection nor live visualization.
The example mechanically compares its workload monotonic origin with the
existing Runner `automatic_run_start` evidence and fails unless the absolute
skew is below 50 ms. This is only planned-schedule clock-alignment evidence;
actual packet timing remains solely tc-BPF telemetry evidence. Every physical
Ping generator has a run-unique container-local identity and PID file, and the
example stops that exact identity with TERM then KILL if needed before verifying
it absent and removing the private evidence. A workload-supervision failure
makes the canonical run fail, but without a Runner cancellation API it may only
be reported after blocking `BpfRunner.run()` returns.

## One-command Planner inspection

From the repository root, run the complete real scenario, Planner publication,
and inspection workflow with the active `skyseed_dev` environment:

```bash
python -m examples.mini_satellite_internet.main
```

The primary artifact is
`output/inspection/planner/index.html`; its sibling JSON, summary, inventory,
and checksum files are private audit evidence. `simu.py` creates the real
Simulator/Slicer, obtains Planner through the public Simulator facade, and
publishes 16 continuous four-Difference Plans covering `(0,4]` through
`(60,64]`; `main.py` passes
those exact immutable results plus
retained PlanningDecisions to the single Tool facade. The Tool itself does not
synchronize Planner or invoke a Model.

The page separates sampled Slice time from publication coverage and nominal
transition time. It presents repeated Connection lifetimes, sampled parameter
values beside actual ParameterTransitions, RoutePatches beside actual
ForwardingTransitions, same-time forwarding precedence, retained coordination
decisions, and Difference-to-Plan comparison. Rejected suppression candidates
remain explicitly unavailable because the formal read surface does not retain
them.

`main.py` is the generation-only deployment-compile, planning,
backend-compile, five-Tool, and control-script entry.
`simu.py` has no CLI and never writes Tool output; its example-local assembly
helpers project the explicitly supplied Emulator into the matching formal
Simulator/Slicer and prepare the Planner branch.

Generation-time `main.py` and generated execution each invoke `compile_bpf`
once per continuous Plan with the explicit previous intended baseline.
`main.py` uses a complete private loop for generated Compiler inspection
evidence; generated execution invokes it one Plan at a time from demand-driven
supplier pulls and never materializes the complete realization chain before
Runner construction. The Tool only consumes
the returned immutable values; it cannot compile, rerun Simulator semantics,
or bind runtime/kernel resources.

The example never edits a Slice, constructs a Connection/Route/Difference
manually, or bypasses the three Models and Slicer. Inventory, observed
behavior, and Difference summaries are produced only by the Slice / Difference
Tool projector from the supplied Slicer. It emits non-authoritative compact
view data, offline per-Slice chunks, HTML, and `summary.txt`; the example
contains no second reporting path.

## Observed deterministic behavior

The accepted Models naturally produce the following current evidence without
editing any result:

- Slice 1 has 32 same-orbit and 16 cross-orbit ISLs;
- all three GroundStations receive access during the bounded run;
- every real Slice has at least one served Zone;
- all 63 post-initial Differences are non-empty;
- later Differences contain 11 Connection additions, 14 removals and 2,530
  directional parameter patches;
- Zone assignments exercise ADDED, CHANGED and REMOVED, including the natural
  `north-america-east` service handover at `00:10:00Z`;
- Route patches exercise ADDED, CHANGED and REMOVED;
- the longest resolved path contains seven hops.

These are reference-scenario observations, not new algorithm guarantees.
The Slicer retains all 64 real Slices and performs no selective omission.
Parameter deadband merging happens in the Planner, while four-Difference
publication batching remains the example-local Planner policy. The current
profile accepts zero connection-suppression candidates; the Tool must not
present any of those mechanisms as Slicer behavior.

## Selection-driven Slice / Difference Tool

The generated directory contains exactly:

- `index.html`: directly openable interactive page with the small manifest
  embedded for offline startup;
- `slice-manifest.json`: private static/timeline view data;
- `chunks/slice-*.js`: one compact offline chunk per retained Slice;
- `summary.txt`: stable counts and observed behavior;
- `inventory.json`: byte sizes and SHA-256 for payload files;
- `checksums.sha256`: hashes for all payload and inventory files.

The timeline includes synthetic Slice 0 and all 64 real Slices, but normal
page load does not parse every Slice. The selected Slice supplies the globe,
node/Connection/Zone details, compact Difference counts, and anomaly-only
diagnostics. Choosing a source and G/Z target restores only that formal path
from the selected chunk's projected next hops; this is browser display logic,
not routing authority.

## Manual review

1. Open `output/inspection/topology/index.html` and select static Orbit rings
   and GroundStations directly on the globe.
2. Open `output/inspection/zone/index.html` and select Zone layout geometry on
   the globe and reference map.
3. Open `output/inspection/slice/index.html`.
4. Confirm Slice 0 shows no timestamp or Position.
5. Move the slider through several real Slices and use Previous/Next.
6. Select Satellite/GroundStation nodes, Connections, and Zones on the current
   globe and confirm details stay selection-scoped.
7. Select several Routing G/Z targets and verify only the chosen path is
   restored.
8. Inspect one Difference category at a time and focus individual patches.
12. Confirm the accepted A–G behavior or record any regression found while
   reviewing the external audit bundle.
13. Open `output/inspection/bpf-compiler/index.html`, select all 16 Plan
    intervals, and inspect Plan-to-symbolic lowering, ordering, and intended
    end state.
14. After a completed or controlled-interrupted `run.sh`, open
    `output/inspection/runner/index.html`; confirm the cleaned lifecycle is
    separate from “Last active frontier”, then inspect timeline, timing,
    Maintenance, recorded default-dataplane evidence, failures, and cleanup.
15. Open `output/inspection/traffic/index.html`, scrub the simulation timeline,
    select an automatically discovered observed flow and Ping, then replay its
    request and reply; compare the inferred source, observed ingress path,
    recorded generation, and elapsed time with the visualization-only animation.
