# Starlink Gen1 satellite Internet example

This example is the reviewable Skyseed v2 Starlink Gen1 satellite-Internet
scenario. Its checked shell catalog contains five formal shell choices, while
the compact scenario selects canonical S3: 36 circular Orbits with 20
Satellites each (720 total) at 570 km altitude and 70° inclination, 355 frozen
evidence-backed GroundStations, and a checked 732-Zone whole-globe layout. Its
one formal Timeline covers 10 minutes at a 5-second interval: 121 timestamped
physical states feed 121 temporary legacy real Slices.

The source layout is intentionally direct:

- `emu.py` defines the upstream Emulator construction;
- `simu.py` defines one Simulator, a Topology-bound physical PositionModel, the
  explicit temporary legacy Slicer, and the existing Planner composition;
- `main.py` selects the final execution stage, advances the rolling domain
  progression, and schedules detached read-only Inspection artifacts.

There is no separate static, short-run, qualification, or runtime Simulator.
A topology-only review obtains the authoritative start-time PhysicalFrame and
leaves the same temporary Slicer unadvanced. A short run uses the same Timeline
and composition but stops at an earlier legacy Slice boundary.

## Inputs

- `scenario.yaml` selects one shell and references the frozen shell and
  GroundStation files;
- `shells.yaml` contains the exact formal S1–S5 FCC shell/orbit/Satellite
  records;
- `ground_stations.yaml` contains 355 reviewed external GroundStation records;
- `zones.yaml` is the explicit formal Zone-layout input loaded into a
  `ZoneLayoutDefinition`.

Normal execution reads only these checked example inputs. It does not read the
repository `research/` tree, generate GroundStations, or regenerate Zones.
The GroundStation set includes supported current, planned, and historical Ka/E
gateway sites and is benchmark geometry, not a simultaneous operational
snapshot. Operator data remains explicit YAML `null` and elevation remains
zero where the reviewed source does not normalize those facts.

## Run through a domain boundary

From `skyseed_v2/`, the default command runs the full generation progression
through Compiler and writes the fixed canonical `output/` tree:

```bash
python -m examples.starlink_gen1_satellite_internet.main
```

Select an explicit earlier generation/progression boundary with:

```bash
python -m examples.starlink_gen1_satellite_internet.main --through slicer
python -m examples.starlink_gen1_satellite_internet.main --through planner
python -m examples.starlink_gen1_satellite_internet.main --through topology
```

The stages are cumulative:

- `topology` creates the one Simulator/Topology/temporary-Slicer composition,
  obtains the start-time PhysicalFrame for inspection, and produces no real
  legacy Slice;
- `slicer` is the explicit temporary legacy stage: it requests PhysicalFrames,
  computes Connection/Route/TopologySlice values and adjacent Differences for
  inspection, then stops before Planner;
- `planner` runs the rolling Topology/PhysicalFrame → legacy Slicer → current
  Planner publication chain and stops before compilation;
- `compiler` calls public `compile_bpf(...)` immediately for each newly
  published Plan, passing the previous immutable realization explicitly, then
  persists that exact ordered realization sequence and writes a generated
  Compose/Runner package. `main.py` does not start the deployment or Runner.

The current Planner uses the checked 50-second planning lookahead and publishes
continuous prefixes of at most 20 seconds. Offline execution runs this rolling
progression as quickly as possible; it does not sleep against wall time and
does not first generate all Slices, then all Plans, then all realizations.

For a shorter debug execution, use an execution-only bound:

```bash
python -m examples.starlink_gen1_satellite_internet.main \
  --through compiler \
  --slice-limit 20
```

`--slice-limit 20` does not create another scenario. The formal Timeline is
still the 10-minute/121-Slice Timeline, and omitting the flag runs to its end.
`--tool-workers` only bounds detached Inspection worker processes and does not
change domain semantics.

## Inspection output

The selected boundary determines the available artifacts under
`output/inspection/`:

| Through | Additional views |
|---|---|
| `topology` | aggregate, Topology, Zone, Physical Frame |
| `slicer` | Slice / Difference, System Observation |
| `planner` | Planner |
| `compiler` | BPF Compiler |

The static Physical Frame Tool receives the exact caller-produced
`Topology + PhysicalFrame` and writes `physical-frame.json`, `index.html`, and
`summary.txt` under `output/inspection/physical-frame/`. The Tool does not call
`Topology.frame_at(...)`, invoke PositionModel, or perform operational
Connection/Route work.

`tool-executions.json` records caller-owned frame preparation as
`input_preparation_wall_seconds`, Tool execution as `tool_wall_seconds`, and
their sum as `wall_seconds`. `input_bytes=0` means the Topology/PhysicalFrame
handoff stayed in process rather than being serialized; it does not mean frame
preparation had zero wall cost. Dynamic modes persist domain observations
before this later inspection-only projection.

Successful `compiler` output also contains the trusted local, checksum-checked
`runtime/realizations/` handoff, `runtime/docker-compose.yml`, and the
standard controls:

```bash
./output/start.sh
SKYSEED_RUNNER_ACTIVATION_LIMIT=3 ./output/run.sh
./output/launch.sh
./output/cleanup.sh
```

The activation limit stops one canonical execution prefix; it does not create
a shorter Timeline or alternate Scenario. Omit it only when the complete
10-minute execution is intended. `start.sh` only starts the already-generated
Compose deployment and records startup duration under
`runtime/deployment-startup.json`. `run.sh` only performs Runner
Preparation/Main/Maintenance/teardown, Traffic execution, and runtime-dependent
Inspection on that already-started deployment. `launch.sh` contains only
`start.sh` followed by `run.sh`; `cleanup.sh` only tears down Compose. Repeated
worker/activation-limit experiments
can reuse the same realization handoff and deployment after each Runner
teardown; `SKYSEED_BPF_RUNTIME_WORKERS` may select a small worker count for an
intentional scaling check.

Compiler output also writes `runtime/skyseed-build-plan.txt`. It hashes every
node build context, builds one representative for each byte-identical group,
aliases that image to the exact tags required by Compose, and then starts with
`--no-build`. The grouping is a generated deployment optimization only: it
does not merge nodes, containers, namespaces, Runner identities, or runtime
state. When `start.sh` produced startup evidence, `run.sh` includes it in the
Runner Tool; Runner attachment itself independently validates the already-live
deployment.

Heavy dynamic Tools use the generic detached process helper. Each process uses
a short-lived checksum-described snapshot under `.inspection-control/`, builds
in staging, audits its bundle, and publishes atomically under
`output/inspection/<tool>/`. The temporary control directory is deleted after
the final join or termination; it is not durable experiment evidence. Worker
failure cannot change domain results. If workers are already occupied, later
Tool submission is deferred until domain execution is complete. Tool rendering
time is not part of domain Observation.

## Selected-shell policy facts

The canonical S3 scenario binds `CircularOrbitPositionModel` to Topology for
physical projection. The temporary legacy Slicer uses
`GeometricConnectionModel` and `GatewayHopCountRouteModel`. Zone service has a
25-degree minimum elevation. GroundStation access has a 5-degree minimum,
3,000 km maximum slant distance, and a global capacity of eight; every selected
Satellite has an explicit two-access cap. Same-orbit and neighbouring-plane ISL
selection remains distance- and Earth-clear-LOS constrained.

The existing normal Planner composition uses sample-boundary Connection,
accumulated-deadband Parameter with the checked scenario numeric configuration,
dependency-aware always-FOLLOW Forwarding, and identity coordination. These
values are scenario choices, not generic Planner numeric defaults.

The isolated 720-Satellite S3 shell is not expected to serve all 732 Zones
continuously. Served percentage is not a coverage-quality target, and the
routing/access settings do not claim to reproduce an operator's live network.

## Offline input maintenance

Regular whole-globe rectangular Zone layouts can be authored independently of
this example:

```bash
python scripts/generate_zone_layout.py \
  --latitude-bands 24 \
  --equatorial-cells 48 \
  --alignment antimeridian-centered \
  --output examples/starlink_gen1_satellite_internet/zones.yaml
```

The settings reproduce all 732 accepted IDs, priorities, geometries, and
ordering. Check mode performs no write:

```bash
python scripts/generate_zone_layout.py \
  --latitude-bands 24 \
  --equatorial-cells 48 \
  --alignment antimeridian-centered \
  --check examples/starlink_gen1_satellite_internet/zones.yaml
```

GroundStation refresh is a separate maintenance operation with a pinned
reviewed-source SHA-256. It never touches `zones.yaml` and is never called by
normal execution:

```bash
python scripts/refresh_starlink_gen1_ground_stations.py \
  --research-source research/starlink/gen1/ground-stations/gateway-evidence-2026-08-25.json \
  --output examples/starlink_gen1_satellite_internet/ground_stations.yaml \
  --check
```

Use `--write` instead of `--check` only when intentionally refreshing the
reviewed frozen input.

The generated runtime consumes the persisted exact Compiler outputs from this
one progression; it does not reconstruct or rerun a second
temporary-Slicer/current-Planner/Compiler algorithm.
