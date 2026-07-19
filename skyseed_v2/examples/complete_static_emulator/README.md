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
created. `operator` is descriptive metadata only. No YAML facade,
GS–Satellite link or dynamic Simulator field exists.

Run the canonical external assembly flow, which uses fixed timestamps and
creates the static Topology without generating a Slice:

```bash
python3 -m examples.complete_static_emulator.simu
```

`simu.py` keeps the user-adjustable start/end/10-second interval values at the
top, then shows `SimulatorStaticInput → SimulationTimeline →
SimulationDefinition.static_input → Simulator → create_topology()` directly.
A bounded Timeline is displayed completely; an unbounded Timeline is displayed
through a fixed four-timestamp prefix without eager materialization.

Generate the complete output from `skyseed_v2/`:

```bash
python3 -m examples.complete_static_emulator.main --override
```

`SkyseedDockerCompiler` expresses locally built SEED base images as Compose
`service:` additional build contexts, so a maintained Docker installation can
use its default BuildKit builder directly. Start the generated project with:

```bash
examples/complete_static_emulator/output/start.sh
```

The generated controls use the exact Compose project
`skyseed_v2_complete_static_emulator`.

Cleanup is project-scoped, idempotent and retains the generated output:

```bash
examples/complete_static_emulator/output/cleanup.sh
```
