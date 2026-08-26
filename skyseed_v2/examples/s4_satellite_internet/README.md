# S4 satellite Internet Simulator qualification

This v2-native example keeps its cheap static S4 review as the default and adds
explicit bounded dynamic and Planner qualification modes. All modes build
one upstream Emulator and project its static owner tree into one
Simulator-owned Topology. The dynamic mode then uses the existing Position →
Connection → Route → Slice → Difference chain. The separate `--planner` mode
continues that exact retained progression through the normal explicit Planner
composition and one BPF Compiler realization per Plan under one Observation
run. No qualification mode enters Runner or containers.

The checked inputs contain:

- six circular orbital planes at 560 km and 97.6 degrees;
- 58 deterministically identified Satellites per plane, 348 total;
- 355 evidence-backed Starlink gateway sites frozen into
  `ground_stations.yaml` from the reviewed 2026-08-25 research selection;
- 732 constellation-independent rectangular Zones in 24 latitude bands.

The qualification ground body includes supported current, planned, and
historical Ka/E gateway sites; it is not a simultaneous operational-network
snapshot. Different records retain different evidence authority in the
persistent research archive. The frozen runtime input contains only the
selected coordinates and stable research IDs. Exact legal-operator and
elevation metadata are not normalized, so every Ground Station keeps an
explicit YAML `null` operator and zero elevation. Runtime construction does not
import or read `research/` or `skyseed_v1`.

From `skyseed_v2/`, run:

```bash
python -m examples.s4_satellite_internet.main
```

Or from this directory:

```bash
python main.py
```

Open:

- `output/inspection/zone/index.html` for the global Zone partition;
- `output/inspection/topology/index.html` for the six static orbital planes,
  348 Satellite identities, 355 Ground Station locations, and 703 total
  dynamic nodes;
- `output/inspection/index.html` for the available-tool landing page.
- after `--planner`, `output/inspection/planner/index.html` and
  `output/inspection/compiler/index.html` for planned and compiled evidence.

The default command intentionally does not create a Slice or invoke a dynamic
Model. Request the first compute-only qualification run explicitly with:

```bash
python -m examples.s4_satellite_internet.main --dynamic
```

That run covers both endpoints of 30 simulated minutes at 10-second intervals
(181 real Slices). Its `GeometricConnectionModel` allows up to eight accesses
per Ground Station, subject to any explicit site cap, and each S4 Satellite has
an explicit two-Ground-Station-access cap. Eligible links are assigned
globally and deterministically: maximize covered Ground Stations first,
minimize aggregate primary candidate rank next, freeze those primary
assignments, and only then fill residual capacity with redundant accesses.
Rank remains higher elevation followed by stable Satellite identity, and
redundancy cannot move or degrade a frozen primary. This is a simple Skyseed
baseline rather than an optimal Starlink operational scheduler. ISLs do not
consume these caps, and there is no active/standby or handover state.

Zone service uses an S4-specific 25-degree minimum elevation at each Zone's
representative point. This accepted user-beam interpretation intentionally
rejects distant low-elevation service; it does not change generic
`GeometricConnectionModel` configuration behavior. The isolated 348-Satellite
shell is not expected to provide continuous global service to all 732 Zones,
and the served-Zone fraction is not a coverage-quality target.

The Gateway-scoped routing model uses three statically selected candidate
Ground Stations per Zone and no geographic cutoff. Candidate selection uses
representative-point geographic distance and stays fixed while current paths
change with each Slice. The access caps and candidate settings are Skyseed
qualification policy parameters, not claims that every Starlink site has the
same hardware. Supporting research is retained under
`research/starlink/gen1/access-capabilities/`. UT/traffic selection is outside
Simulator progression. Compact raw metrics are written under
`output/qualification/`. The maintained manual Slice / Difference Tool belongs
at `output/inspection/slice/index.html`; it uses one lazy offline chunk per
retained Slice and keeps only the selected chunk in the browser. Build-only
timing, profiling, and checksum evidence may reference that canonical Tool
bundle instead of duplicating it. The browser artifact does not become a
second simulation authority or eagerly expand every Route path. Its compact
current-Slice and model-context cards project the scenario policy and current
formal counts at generation time; physical separation statistics and the
approximate service-envelope radius are explicitly Tool-derived explanation.

Qualify the normal Planner composition on a short S4-20 progression with:

```bash
python -m examples.s4_satellite_internet.main --planner --slice-limit 20
```

The normal composition directly selects sample-boundary Connection,
accumulated-deadband Parameter with the existing S4 absolute/relative,
persistence, max-staleness and bundling configuration, dependency-aware
always-FOLLOW Forwarding, example-local identity coordination, and the
50-second planning-lookahead / 20-second publication window. Connection and
Forwarding therefore translate formal evolution faithfully; only Parameter
may coalesce updates. It has no bundled production profile identity.

Named profiles remain diagnostic helpers. `reference-global` uses
all-available planning and publication plus the fully sample-boundary reference
family. `reference-bounded` is its fair rolling/publication control.
`stable-bounded` and `experimental-stable-global` preserve the optional Stable
Connection, Stable Forwarding, and dependency-aware coordination evidence
compositions; neither is selected by the normal command.
The S4-specific durations and stability thresholds are qualification evidence
points; they do not become generic Planner defaults.

Every Planner composition prints one evidence checkpoint after each Plan
publication, retains caller-owned Plans for inspection, and compiles one
continuous immutable BPF realization per Plan. It validates symbolic Compiler
output only; no kernel/runtime work occurs. Omitting `--slice-limit` requests
the canonical full 181-Slice / 90-Plan progression.

Heavy Slice, Planner, Compiler, and Observation Tool generation is detached
from the domain critical path. Two independent family-level worker processes
are allowed by default (`--tool-workers` changes this bounded execution setting
without changing domain semantics). Slice Tool work starts after Slicer;
Planner Tool work starts after Planner while Compiler proceeds. The CLI joins
only for final artifact completeness. `qualification/run.json` records separate
Domain/Inspection statuses, PIDs, wall time, peak RSS, and actual overlap.
Deterministic frozen handoffs under `qualification/tool-handoffs/` permit a
Tool-only rebuild without rerunning Slicer → Planner → Compiler; they are
trusted repository-local gzip/pickle evidence, not an interchange format.

The short qualification baseline retains Slice 0 plus 20 real 10-second Slices
and 20 adjacent Differences. Across
the real Slices, served Zones are 419 / 421 / 424 min/median/max. On the
qualification host, warm Position, Connection, Route, complete-Slice, and
Difference p50/p95 timings were respectively 0.0084/0.0087,
0.3245/0.3917, 0.4581/0.5044, 0.8263/0.8952, and 0.0401/0.0674 seconds.
These are bounded-run evidence, not general performance guarantees.

No S4 qualification mode renders or compiles a Docker deployment. The default
and `--dynamic` modes do not create a Planner or BPF realization; only explicit
`--planner` enters Planner and the symbolic BPF Compiler.

## Deterministic inputs

`scenario.yaml` uses the general generated-orbit configuration with the
FCC-derived plane/phase pattern preserved in source-plane order. RAAN starts at
63.7 degrees and advances by 60 degrees. The in-plane phase step is the derived
`360/58`, and each successive Orbit's start phase advances by the derived
`360/348`. These formulas reproduce all 348 explicit one-decimal mean anomalies
preserved under `reference/`; the formulas do not replace those authoritative
records.

The FCC geometry and phase pattern are re-anchored at the Skyseed benchmark
epoch `2026-01-01T00:00:00Z`. This does not claim to propagate the real
constellation from the filing's 2015 epoch. The separate alternating inter-Orbit
phase policy remains supported for scenarios such as the canonical Mini. The
existing circular Position Model supplies eccentricity zero by construction;
it has no separate argument-of-perigee field.

Each Zone band is divided into equal-width longitude cells with cell zero
centered on, and formally wrapping, the antimeridian. This deterministic
half-cell rotation makes both sides of the existing wrapped-rectangle semantics
visible without changing cell counts or physical dimensions.

`generate_inputs.py` owns the example-local mechanical generation rule for
`zones.yaml` and the explicit, hash-guarded conversion of the reviewed research
selection into the existing v2 Ground Station schema. It introduces no public
generator API. Verify the checked files with:

```bash
python examples/s4_satellite_internet/generate_inputs.py --check
```

Regeneration of the frozen Ground Station file is an explicit maintenance
operation:

```bash
python examples/s4_satellite_internet/generate_inputs.py \
  --write \
  --research-ground-stations \
  research/starlink/gen1/ground-stations/gateway-evidence-2026-08-25.json
```

Normal construction and input verification use only the checked example-local
v2 files. The persistent `research/` archive remains non-runtime evidence.
