# Self-Contained Skyseed v2 Examples

Every formal experiment owns a thin entrypoint, scoped cleanup and ignored
output directory. The two teaching examples are handwritten; only the scale
example owns v2 YAML:

```text
examples/
├── isolated_satellites/
│   ├── main.py
│   ├── README.md
│   ├── start.sh
│   └── cleanup.sh
├── complete_static_emulator/
│   ├── main.py
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

The small examples expose the real public ownership path directly:

```text
handwritten values
    -> SkyseedAutonomousSystem.create_shell
    -> Shell.create_orbit
    -> Orbit.create_satellite
    -> SkyseedAutonomousSystem.create_ground_station (complete example)
```

The scale example uses the one file-input path:

```text
v2 YAML
    -> StaticEmulatorScenarioLoader.load
    -> immutable StaticEmulatorScenarioSpecification
    -> StaticEmulatorSpecificationApplier.apply
    -> the same public domain create methods
    -> SimulationEntityCatalog
```

The Loader is a serialization adapter. The Applier is an optional application
service for complete specifications, not a domain entity and not a required
handwritten entrypoint. Multiple Shells remain a collection owned by an AS;
there is no population or `Constellation` domain object.

The `reference/` directory is excluded from the package and formal example
path. It preserves offline historical investigation/conversion tooling only;
no formal example imports or reads it.

See each experiment's README for exact commands. `SkyseedDockerCompiler`
declares locally built SEED base images as Compose `service:` additional build
contexts, and the active examples use the maintained Docker Engine BuildKit
and plugin-based `docker compose` defaults directly. They do not select the
legacy builder.
