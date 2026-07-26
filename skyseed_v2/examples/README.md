# Self-Contained Skyseed v2 Examples

Every deployment experiment owns a thin entrypoint, scoped cleanup and ignored
output directory. The two teaching deployment examples are handwritten; the
static scale example and richer Dynamic Slice reference own explicit v2 YAML:

```text
examples/
├── isolated_satellites/
│   ├── main.py
│   └── README.md
├── complete_static_emulator/
│   ├── emu.py
│   ├── main.py
│   ├── simu.py
│   └── README.md
├── large_static_network/
│   ├── main.py
│   ├── scenario.yaml
│   ├── ground_stations.yaml
│   └── README.md
└── dynamic_slice_network/
    ├── scenario.yaml
    ├── zones.yaml
    ├── emu.py
    ├── simu.py
    ├── main.py
    └── README.md
```

| Example | Long-term purpose | Recommended entry | Compose | Simulator / Slices | Inspection Tools |
| --- | --- | --- | --- | --- | --- |
| `isolated_satellites` | Specialized handwritten two-Satellite deployment | `python3 -m examples.isolated_satellites.main` | Yes | No | None |
| `complete_static_emulator` | Minimal static/canonical example kept deliberately small | `python3 -m examples.complete_static_emulator.main` | Yes | One static Topology in `main.py`; separate `simu.py` is the small A–F smoke | Topology and Zone |
| `large_static_network` | Specialized static scale and Compose validation | `python3 -m examples.large_static_network.main` | Yes | Static input projection only | None |
| `dynamic_slice_network` | Comprehensive Emu + Simu reference for final manual review | `python3 -m examples.dynamic_slice_network.main` | Yes | One Simulator/Slicer with the bounded A–F sequence | Topology, Zone and five-view Dynamic Slice |

Running an example's Python entrypoint generates its ignored `output/`:

```text
output/
├── start.sh
├── cleanup.sh
├── runtime/
│   ├── docker-compose.yml
│   └── compiler-generated build contexts
└── inspection/              # only examples with read-only Tools
    └── one isolated subtree per Tool
```

`runtime/` is the runnable static container project. `inspection/` contains
only read-only offline views and their private audit payloads. Examples
without Tools omit `inspection/`; no example copies runtime files or Tool
payloads into the output root.

Each formal example can be run directly from its own directory with
`python3 main.py`. Direct-file execution always uses the interpreter selected
by the caller; it never searches for or restarts through another environment.
For local development, create and activate the repository's canonical
`skyseed_dev` Conda environment as described in the top-level README.
Compilation entrypoints replace their owned default output unless
`--no-override` is passed. From that same directory, start and stop the
generated project with
`./output/start.sh` and `./output/cleanup.sh`.

Project names are fixed and distinct. Generated `start.sh` validates the exact
`runtime/docker-compose.yml` and starts that project with `up -d --build`.
Generated
`cleanup.sh` runs project-scoped `down --remove-orphans` and keeps the output
available for inspection or another start. Neither script is a Runner, and no
script uses global prune, shared-image deletion, recovery logic or fallback.

The small examples expose the real public ownership path directly:

```text
handwritten values
    -> SkyseedAutonomousSystem.create_shell
    -> Shell.create_orbit
    -> Orbit.create_satellite
    -> SkyseedAutonomousSystem.create_ground_station (complete example)
```

The static scale and richer dynamic examples use YAML because repeated input
records are data rather than teaching code. Both use the one file-input path:

```text
v2 YAML
    -> load_static_emulator_config(path)
    -> immutable StaticEmulatorConfig
    -> create_static_emulator(config)
    -> the same public entity create methods
    -> upstream Emulator
    -> Emulator.getLayer("Base")
    -> SatelliteBase.get_skyseed_autonomous_systems()
    -> create_simulator_static_input(skyseed_autonomous_systems)
    -> immutable SimulatorStaticInput
```

The loading, creation and input-generation operations are direct functions,
not stateless service classes. `create_static_emulator` returns the actual
upstream Emulator and does not generate Simulator static input. Multiple Shells
remain a collection owned by an AS; there is no population or `Constellation`
entity.

Both input styles converge at `SkyseedAutonomousSystem.create_shell`,
`Shell.create_orbit`, `Orbit.create_satellite` and
`SkyseedAutonomousSystem.create_ground_station`. `SimulatorStaticInput` is the
immutable static-data boundary consumed by Simulator construction; it is not a
scenario, SEED Registry, container inventory or output-directory index.
Entity traversal and conversion belong to `emulator_to_simulator.py`;
the receiving-side `skyseed_v2.simulator.input` package contains only
immutable data and its pure-data lookup. No public/global
`NodeId → entity/service/container` mapping exists. Any future
dynamic-execution lookup will call the existing Base/AS/Shell/Orbit owners
directly.

`complete_static_emulator/emu.py` is the shared construction helper and returns
only the upstream Emulator. `main.py` reuses it for the canonical full output:
one Simulator/Topology from the same Emulator, the preserved render/compile
path, and the offline Topology/Zone tool snapshot after compilation. `simu.py`
independently reuses it as the readable external assembly entrypoint, queries
all Skyseed AS values from `SatelliteBase`, and then shows the Simulator-side
flow directly:

```text
create_simulator_static_input(...)
→ SimulationTimeline
→ SimulationDefinition
→ Simulator
→ explicit create_topology()
→ read-only Topology facade queries
```

It creates no dynamic Slice and does not render or compile deployment output.
It also does not generate tool artifacts.
For an unbounded Timeline it displays only a fixed prefix and never consumes
the infinite iterator in full.

The `reference/` directory is excluded from the package and formal example
path. It preserves offline historical investigation/conversion tooling only;
no formal example imports or reads it.

`dynamic_slice_network/` is the richer bounded Dynamic Slice reference used by
all three Inspection Tools. It remains separate from the minimal canonical
smoke. Its `emu.py` is the sole Emulator builder, `simu.py` only projects an
explicitly supplied existing Emulator, and `main.py` is the sole executable
assembly. That entry compiles the current static Docker/Compose network,
projects the same SatelliteBase owner into one exact Slicer, generates every
Slice/Difference, gives the Slicer's exact Topology to the Topology and Zone
Tools, and gives the exact Slicer to the five-view Dynamic Slice Tool. Static
container artifacts remain under `output/runtime`; all three read-only views
remain under `output/inspection`. Static containers are not updated by Slice
results.

See each experiment's README for exact commands. `SkyseedDockerCompiler`
declares locally built SEED base images as Compose `service:` additional build
contexts, and the active examples use the maintained Docker Engine BuildKit
and plugin-based `docker compose` defaults directly. They do not select the
legacy builder.
