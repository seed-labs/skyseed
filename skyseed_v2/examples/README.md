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
│   ├── main.py
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
    -> StaticEmulatorCreationResult
    -> create_simulator_static_input(creation.skyseed_autonomous_systems)
    -> immutable SimulatorStaticInput
```

The three operations are direct functions, not stateless service classes.
`create_static_emulator` does not generate Simulator input. Multiple Shells
remain a collection owned by an AS; there is no population or `Constellation`
entity.

Both input styles converge at `SkyseedAutonomousSystem.create_shell`,
`Shell.create_orbit`, `Orbit.create_satellite` and
`SkyseedAutonomousSystem.create_ground_station`. `SimulatorStaticInput` is the
immutable static-data boundary for future Simulator consumers; it is not a
scenario, SEED Registry, container inventory or output-directory index.
Entity traversal and conversion belong to `simulator_input_generation.py`;
the Simulator-owned `skyseed_v2.simulator.static_input` package contains only
immutable data and its pure-data lookup. No public/global
`NodeId → entity/service/container` mapping exists. Any future
dynamic-execution lookup will call the existing Base/AS/Shell/Orbit owners
directly.

The `reference/` directory is excluded from the package and formal example
path. It preserves offline historical investigation/conversion tooling only;
no formal example imports or reads it.

See each experiment's README for exact commands. `SkyseedDockerCompiler`
declares locally built SEED base images as Compose `service:` additional build
contexts, and the active examples use the maintained Docker Engine BuildKit
and plugin-based `docker compose` defaults directly. They do not select the
legacy builder.
