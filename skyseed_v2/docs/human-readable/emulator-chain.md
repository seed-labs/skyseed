# Emulator owner and construction chain

Legend: `A --> B` means B receives the output of the preceding construction
stage. Ownership cardinality is shown only in [the object model](object-model.md).

```mermaid
flowchart TD
    YAML["scenario / ground-station YAML"] --> CFG["StaticEmulatorConfig"]
    CFG --> CREATE["create_static_emulator()"]
    CREATE --> EMU["upstream Emulator"]
    EMU --> READY["complete owner tree + SEED routing layers"]
```

Legend: every `A ->> B` message is a direct call from A to B; returns are
omitted.

```mermaid
sequenceDiagram
    participant User
    participant Config
    participant Construction
    participant Emulator
    participant Owners
    User->>Config: load or construct immutable declarations
    User->>Construction: create_static_emulator(config)
    Construction->>Emulator: create upstream composition
    Construction->>Owners: create AS → Shell/GS → Orbit → Satellite
    Construction->>Emulator: add Base and routing layers
```

Code entry: `src/skyseed_v2/emulator/construction.py`. Human examples:
`examples/complete_static_emulator/`, `examples/mini_satellite_internet/`, and
the static scale-review input in `examples/starlink_gen1_satellite_internet/`.
Implementation state is recorded only in [`CURRENT.md`](../CURRENT.md).
