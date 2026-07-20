# Complete Static Emulator

This handwritten example shows the complete public composition: two isolated
Satellites, one GroundStation, one ordinary SEED AS/Network/Router, one IX, one
explicit eBGP peering, immutable Emulator-to-Simulator input and
`SkyseedDockerCompiler`.

`emu.py` contains only `build_emulator() -> Emulator`. `main.py` reuses it for
the pure Emulator render/compile path. `simu.py` reuses it for external
Emulator-to-Simulator assembly, obtains all Skyseed AS values from the real
`SatelliteBase` owner and passes that collection to
`create_simulator_static_input(...)`. The ordinary AS is excluded by that
owner query. No NodeId-to-Emulator-entity mapping or resolution path is
created. `operator` is descriptive metadata only. The Simulator path loads the
static Zone layout from `zones.yaml`; no GS–Satellite link or dynamic Simulator
field exists.

From `skyseed_v2/examples/complete_static_emulator/`, run the canonical
external assembly flow, which uses fixed timestamps and creates the static
Topology without generating a Slice:

```bash
python3 simu.py
```

The equivalent module command from `skyseed_v2/` is
`python3 -m examples.complete_static_emulator.simu`.

`simu.py` keeps the user-adjustable start/end/10-second interval values at the
top, then shows `SimulatorStaticInput → SimulationTimeline →
SimulationDefinition.static_input → Simulator → create_topology(...)` directly,
then demonstrates physical Zone matches and authoritative resolution.
A bounded Timeline is displayed completely; an unbounded Timeline is displayed
through a fixed four-timestamp prefix without eager materialization.

For a layout with many Zones, defining them in `zones.yaml` is the recommended
path. Small tests may instead construct
`LatitudeLongitudeRectangleDefinition`, `ZoneDefinition` and
`ZoneLayoutDefinition` directly. Both paths produce the same
`ZoneLayoutDefinition` before `Simulator.create_topology(...)`; the YAML loader
does not introduce a second Zone representation.

The latitude/longitude pairs printed by this example are manually chosen query
points, not new domain entities. A physical match is every Zone whose closed
boundary contains the point; the authoritative result is the one match chosen
by the active `ZoneMembershipModel`. The example neither computes Satellite
positions nor selects a service Satellite for a Zone.

Generate the complete output from the same example directory:

```bash
python3 main.py
```

Direct-file execution uses the repository `.venv` when the current Python
lacks the installed dependencies. Existing default output is replaced
automatically; pass `--no-override` to reject an existing output. The
equivalent module command from `skyseed_v2/` is
`python3 -m examples.complete_static_emulator.main`.

`SkyseedDockerCompiler` expresses locally built SEED base images as Compose
`service:` additional build contexts, so a maintained Docker installation can
use its default BuildKit builder directly. Start the generated project with:

```bash
./output/start.sh
```

The generated controls use the exact Compose project
`skyseed_v2_complete_static_emulator`.

Cleanup is project-scoped, idempotent and retains the generated output:

```bash
./output/cleanup.sh
```
