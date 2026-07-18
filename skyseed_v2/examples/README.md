# Self-Contained Skyseed v2 Examples

Every formal experiment owns its v2 scenario data, thin entrypoint, scoped
cleanup and ignored output directory:

```text
examples/
├── isolated_satellites/
│   ├── main.py
│   ├── scenario.yaml
│   ├── README.md
│   ├── start.sh
│   └── cleanup.sh
├── complete_static_emulator/
│   ├── main.py
│   ├── scenario.yaml
│   ├── README.md
│   ├── start.sh
│   └── cleanup.sh
└── large_static_network/
    ├── main.py
    ├── scenario.yaml
    ├── ground_stations.yaml
    ├── README.md
    ├── start.sh
    └── cleanup.sh
```

`output/` is the default beneath each experiment and is ignored by Git.
Project names are fixed and distinct. Each `start.sh` only executes its local
Python entrypoint; it is not a Runner. Each `cleanup.sh` uses that experiment's
exact Compose project/file and removes only its local output. No script uses a
global Docker prune, shared-image deletion, recovery logic or hidden fallback.

The formal examples all follow one input path:

```text
example-local v2 YAML
    -> StaticEmulatorScenarioLoader
    -> immutable generic specifications
    -> StaticEmulatorScenarioBuilder
    -> SkyseedAutonomousSystem -> Shell -> Orbit -> Satellite
                             \-> GroundStation
    -> SimulationEntityCatalog
    -> SkyseedDockerCompiler
```

The loader and builder are application services, not domain entities. Multiple
Shells are merely a collection owned by an AS/scenario; there is no
`Constellation` domain object.

The `reference/` directory is excluded from the package and formal example
path. It preserves offline historical investigation/conversion tooling only;
no formal example imports or reads it.

See each experiment's README for exact commands. `SkyseedDockerCompiler`
declares locally built SEED base images as Compose `service:` additional build
contexts, and the active examples use the maintained Docker Engine BuildKit
and plugin-based `docker compose` defaults directly. They do not select the
legacy builder.
