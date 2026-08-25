# Complete Static Emulator

This handwritten example shows the complete public composition: two planned
Satellites, one GroundStation, one ordinary SEED AS/Network/Router, one IX, one
explicit eBGP peering, immutable Emulator-to-Simulator input, the accepted
Slice / Difference A–E chain, accepted F TopologyDifference evidence, the
Simulator-owned semantic Planner, and the upstream SEED `Docker` compiler.

`emu.py` contains only `build_emulator() -> Emulator`. `main.py` builds that
Emulator once, assembles one Simulator/Topology from the same object, preserves
the Emulator render/compile path, then writes the offline Topology and Zone
tool snapshot. `simu.py` independently reuses it for readable external
Emulator-to-Simulator assembly and obtains all Skyseed AS values from the real
upstream `Base` owner with `get_skyseed_autonomous_systems(base)` and passes
that collection to
`create_simulator_static_input(...)`. The ordinary AS is excluded by that
owner query. No NodeId-to-Emulator-entity mapping or resolution path is
created. `operator` is explicit `str | None` descriptive metadata only; this
example supplies a known string. The Simulator path loads the
static Zone layout from `zones.yaml`; formal dynamic Connections remain
Simulator results and do not mutate Emulator entities.

From `skyseed_v2/examples/complete_static_emulator/`, run the canonical
external assembly flow, which uses fixed timestamps, explicitly creates the
three built-in Slicer Models, Slicer and Planner, queries synthetic Slice 0,
generates two real TopologySlices, publishes an initial Plan, then creates a
third Slice and incrementally publishes the next continuous non-overlapping
Plan interval:

```bash
python3 simu.py
```

The equivalent module command from `skyseed_v2/` is
`python3 -m examples.complete_static_emulator.simu`.

`simu.py` keeps the user-adjustable start/end/10-second interval values at the
top, then shows `SimulatorStaticInput → SimulationTimeline →
SimulationDefinition.static_input → Simulator → create_topology(...)` directly,
then demonstrates Position, ISL, GroundStation access, Zone service, G/Z next
hops, a resolved full path and a legal unserved Zone. A bounded Timeline is
displayed completely; an unbounded Timeline is displayed through a fixed
four-timestamp prefix without eager materialization.

The Planner path uses midpoint Connection timing, accumulated parameter
deadbands, dependency-aware forwarding and the default CoordinationPolicy. It
prints Plan coverage, typed transition/Forwarding-precedence counts, and one
retained/merged/suppressed decision record. The precedence is a same-time
semantic constraint, not runtime execution order. These are semantic planning
results only; no backend or runtime operation is produced.

For a layout with many Zones, defining them in `zones.yaml` is the recommended
path. Small tests may instead construct
`LatitudeLongitudeRectangleDefinition`, `ZoneDefinition` and
`ZoneLayoutDefinition` directly. Both paths produce the same
`ZoneLayoutDefinition` before `Simulator.create_topology(...)`; the YAML loader
does not introduce a second Zone representation.

The latitude/longitude pairs printed by this example are manually chosen query
points, not new domain entities. A physical match is every Zone whose closed
boundary contains the point; the authoritative result is the one match chosen
by the active `ZoneMembershipModel`. Static membership remains separate from
the dynamic service assignment shown by the ConnectionSlice.

Generate the complete output from the same example directory:

```bash
python3 main.py
```

The default command compiles the runnable static project under
`output/runtime/` and writes `output/inspection/index.html` plus standalone
Topology and Zone pages:

```text
output/
├── start.sh
├── cleanup.sh
├── runtime/
│   └── docker-compose.yml and compiler-generated files
└── inspection/
    ├── index.html
    ├── topology/
    └── zone/
```

The pages embed their data, work offline when opened directly, and retain
private JSON audit artifacts only inside their Tool subtree; `simu.py`
deliberately does not generate these tools.

Direct-file execution uses the caller's selected Python and never switches
environments. The recommended local interpreter is the canonical
`skyseed_dev` Conda environment described in the top-level README. Existing
default output is replaced automatically; pass `--no-override` to reject an
existing output. The equivalent module command from `skyseed_v2/` is
`python3 -m examples.complete_static_emulator.main`.

The canonical command enables the standard upstream InternetMap image. Use
`--no-internet-map` only to isolate a suspected Map failure during diagnosis.
The example does not stage a local Map source tree or infer the SEED checkout
layout.

Start the generated upstream SEED `Docker` Compose project with:

```bash
./output/start.sh
```

The generated controls use the exact Compose project
`skyseed_v2_complete_static_emulator`.

Cleanup is project-scoped, idempotent and retains the generated output:

```bash
./output/cleanup.sh
```
