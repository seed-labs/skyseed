# Production package import map

Legend: every `A --> B` edge means at least one current Python module in
package group A directly imports a module in group B. No ownership, creation,
data flow, or “receives from” meaning is encoded here. Same-group imports are
collapsed. The gate derives this exact graph from production AST imports.

```mermaid
flowchart LR
    ROOT["skyseed_v2 facade"]
    APP["private generated BPF application composition"]
    SHARED["shared"]
    CONFIG["configuration facade/shared YAML"]
    CONFIG_EMU["configuration/emulator"]
    CONFIG_SIM["configuration/simulator"]
    EMU["emulator facade/construction"]
    ENTITIES["emulator/entities"]
    SEED["emulator/seedemu"]
    BRIDGE["emulator_to_simulator"]
    INPUT["simulator/input"]
    SIM["simulator facade/definition/timeline/root"]
    GEOMETRY["simulator/geometry"]
    TOPOLOGY["simulator/topology"]
    SLICING["simulator/slicing"]
    MODELS["simulator/models"]
    PLANNING["simulator/planning"]
    COMPOSE["compose"]
    BPF["backend/bpf"]

    APP --> BPF
    APP --> SHARED
    APP --> SIM
    BRIDGE --> ENTITIES
    BRIDGE --> INPUT
    BRIDGE --> SEED
    BRIDGE --> SHARED
    CONFIG --> CONFIG_EMU
    CONFIG --> CONFIG_SIM
    CONFIG --> SHARED
    CONFIG_EMU --> CONFIG
    CONFIG_EMU --> SHARED
    CONFIG_SIM --> CONFIG
    CONFIG_SIM --> SHARED
    EMU --> CONFIG_EMU
    EMU --> ENTITIES
    EMU --> SEED
    ENTITIES --> SHARED
    GEOMETRY --> SHARED
    INPUT --> SHARED
    MODELS --> GEOMETRY
    MODELS --> SHARED
    MODELS --> SLICING
    MODELS --> TOPOLOGY
    ROOT --> EMU
    ROOT --> ENTITIES
    SEED --> ENTITIES
    SEED --> SHARED
    SIM --> CONFIG_SIM
    SIM --> GEOMETRY
    SIM --> INPUT
    SIM --> MODELS
    SIM --> PLANNING
    SIM --> SHARED
    SIM --> SLICING
    SIM --> TOPOLOGY
    SLICING --> GEOMETRY
    SLICING --> SHARED
    SLICING --> SIM
    SLICING --> TOPOLOGY
    PLANNING --> SHARED
    PLANNING --> SIM
    PLANNING --> SLICING
    TOPOLOGY --> CONFIG_SIM
    TOPOLOGY --> INPUT
    TOPOLOGY --> SHARED
    BPF --> SHARED
    BPF --> SIM
```

`APP` is the single root-private generated-experiment composition entry and is
not a BPF backend package. `COMPOSE` has no outgoing production-package
dependency. `BPF` consumes public
Simulator/shared facts and no Compose package object. Simulator and Compose
have no reverse edge into BPF. External
stdlib/dependency imports, including upstream SEED, are outside this internal
graph. Module-level dependency validation remains acyclic even where collapsed
facade groups show edges in both directions.

Forbidden reverse dependencies remain Simulator core → Emulator/SEED,
production → Tools, and SEED integration → Simulator/Tools/Compose/examples.
Repository-level examples, Tools, tests, and scripts may import production;
production does not import them.

Current implementation state is maintained only in
[`CURRENT.md`](../CURRENT.md).
