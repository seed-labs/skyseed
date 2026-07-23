# Self-Contained Skyseed v2 Examples

Every formal experiment owns a thin entrypoint, scoped cleanup and ignored
output directory. The two teaching examples are handwritten; only the scale
example owns v2 YAML:

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
└── large_static_network/
    ├── main.py
    ├── scenario.yaml
    ├── ground_stations.yaml
    └── README.md
```

Running an example's Python entrypoint generates its ignored `output/`:

```text
main.py
    → output/docker-compose.yml and build contexts
    → output/start.sh
    → output/cleanup.sh
```

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
Compose file and starts that project with `up -d --build`. Generated
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

Only the scale example uses YAML because hundreds of repeated input records are
data rather than teaching code. It uses the one file-input path:

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
Entity traversal and conversion belong to `simulator_input_generation.py`;
the receiving-side `skyseed_v2.simulator.static_input` package contains only
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

See each experiment's README for exact commands. `SkyseedDockerCompiler`
declares locally built SEED base images as Compose `service:` additional build
contexts, and the active examples use the maintained Docker Engine BuildKit
and plugin-based `docker compose` defaults directly. They do not select the
legacy builder.
