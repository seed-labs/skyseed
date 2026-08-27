# S4 satellite Internet example

This example is the large, reviewable Skyseed v2 satellite-Internet scenario.
It builds six circular orbital planes with 58 Satellites each (348 total), 355
frozen evidence-backed GroundStations, and a checked 732-Zone whole-globe
layout. Its one formal Timeline covers 30 minutes at a 10-second interval (181
real Slices).

The source layout is intentionally direct:

- `emu.py` defines the upstream Emulator construction;
- `simu.py` defines one Simulator, Topology, Slicer, and accepted Planner
  composition;
- `main.py` selects the final execution stage, advances the rolling domain
  progression, and schedules detached read-only Inspection artifacts.

There is no separate static, short-run, qualification, or runtime Simulator.
A topology-only review simply leaves the same Slicer unadvanced. A short run
uses the same Timeline and composition but stops at an earlier Slice boundary.

## Inputs

- `scenario.yaml` owns the shell/orbit configuration and references the frozen
  GroundStation file;
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

From `skyseed_v2/`, the default command builds the complete normal composition,
does not advance Slicer, and writes only the aggregate, Topology, and Zone
views:

```bash
python -m examples.s4_satellite_internet.main
```

Select a later early-stop boundary with:

```bash
python -m examples.s4_satellite_internet.main --through slicer
python -m examples.s4_satellite_internet.main --through planner
python -m examples.s4_satellite_internet.main --through compiler
```

The stages are cumulative:

- `topology` creates the one Simulator/Topology/Slicer composition without
  producing a real Slice;
- `slicer` advances that Slicer and produces adjacent formal Differences for
  inspection, then stops before Planner;
- `planner` runs the rolling Slicer → Planner publication chain and stops
  before compilation;
- `compiler` calls public `compile_bpf(...)` immediately for each newly
  published Plan, passing the previous immutable realization explicitly, then
  stops before Runner.

Planner uses the accepted 50-second planning lookahead and publishes continuous
prefixes of at most 20 seconds. Offline execution runs this rolling progression
as quickly as possible; it does not sleep against wall time and does not first
generate all Slices, then all Plans, then all realizations.

For a shorter debug execution, use an execution-only bound:

```bash
python -m examples.s4_satellite_internet.main \
  --through compiler \
  --slice-limit 20
```

`--slice-limit 20` does not create an S4-20 scenario. The formal Timeline is
still the 30-minute/181-Slice S4 Timeline, and omitting the flag runs to its end.
`--tool-workers` only bounds detached Inspection worker processes and does not
change domain semantics.

## Inspection output

The selected boundary determines the available artifacts under
`output/inspection/`:

| Through | Additional views |
|---|---|
| `topology` | aggregate, Topology, Zone |
| `slicer` | Slice / Difference, System Observation |
| `planner` | Planner |
| `compiler` | BPF Compiler |

Heavy dynamic Tools use the generic detached process helper. Each process
freezes a deterministic checksum-described handoff under
`output/inspection/_execution/tool-handoffs/`, builds in staging, audits its
bundle, and publishes atomically. Domain and Inspection statuses are separate;
worker failure cannot change domain results. If workers are already occupied,
later Tool submission is deferred until domain execution is complete. Tool
rendering time is not part of domain Observation.

The neutral `_execution/status.json` is a small execution/artifact status file,
not a second domain result or a large run report. A preserved handoff can be
rebuilt without rerunning the domain chain:

```bash
python scripts/rebuild_inspection_from_handoff.py \
  examples/s4_satellite_internet/output/inspection/_execution/tool-handoffs/planner.json
```

## S4 policy facts

The accepted scenario uses `CircularOrbitPositionModel`,
`GeometricConnectionModel`, and `GatewayHopCountRouteModel`. Zone service has a
25-degree minimum elevation. GroundStation access has a 5-degree minimum,
3,000 km maximum slant distance, and a global capacity of eight; every S4
Satellite has an explicit two-access cap. Same-orbit and neighbouring-plane ISL
selection remains distance- and Earth-clear-LOS constrained.

The accepted normal Planner composition uses sample-boundary Connection,
accumulated-deadband Parameter with the checked S4 numeric configuration,
dependency-aware always-FOLLOW Forwarding, and identity coordination. These
values are S4 scenario choices, not generic Planner numeric defaults.

The isolated 348-Satellite shell is not expected to serve all 732 Zones
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
  --output examples/s4_satellite_internet/zones.yaml
```

The S4 settings reproduce all 732 accepted IDs, priorities, geometries, and
ordering. Check mode performs no write:

```bash
python scripts/generate_zone_layout.py \
  --latitude-bands 24 \
  --equatorial-cells 48 \
  --alignment antimeridian-centered \
  --check examples/s4_satellite_internet/zones.yaml
```

GroundStation refresh is a separate S4 maintenance operation with a pinned
reviewed-source SHA-256. It never touches `zones.yaml` and is never called by
normal execution:

```bash
python scripts/refresh_s4_ground_stations.py \
  --research-source research/starlink/gen1/ground-stations/gateway-evidence-2026-08-25.json \
  --output examples/s4_satellite_internet/ground_stations.yaml \
  --check
```

Use `--write` instead of `--check` only when intentionally refreshing the
reviewed frozen input.

Runner is not entered by this example path in this round. A future S4 Runner
scale stage must consume the same ordered realization progression; it must not
introduce another Slicer/Planner/Compiler algorithm.
