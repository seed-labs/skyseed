# System overview

Legend: `A --> B` means B is the next system boundary receiving A's formal
output or control handoff. It does not express object ownership.

```mermaid
flowchart LR
    CFG["Emulator configuration"] --> CON["create_static_emulator"]
    CON --> EMU["upstream SEED Emulator<br/>Skyseed entity owner tree"]
    EMU --> BOUND["create_simulator_static_input"]
    BOUND --> DTO["immutable SimulatorStaticInput"]
    DTO --> DEF["SimulationDefinition + Timeline"]
    DEF --> SIM["Simulator"]
    SIM --> TOP["one Topology"]
    SIM --> SLI["one Slicer"]
    SLI --> RES["retained Slice / Difference"]
    SIM --> PLA["at most one Planner"]
    RES --> PLA
    PLA --> PLAN["immutable semantic Plan"]
    PLAN --> BPF["one-shot compile_bpf"]
    RES --> BPF
    BPF --> REAL["immutable symbolic BpfCompiledRealization"]
    RES --> TOOL["read-only Inspection Tools"]

    EMU --> COMP["upstream SEED Docker compiler"]
    COMP --> PROJECT["Compose project"]
    PROJECT --> LAUNCH["ComposeProjectLauncher"]
```

The upper dynamic path and lower static deployment path share the Emulator
source but not lifecycle ownership. Tools only observe formal objects/results.
The BPF Compiler is a downstream reader, not a Simulator-owned component; it
does not bind containers or kernel resources. Any future shared container
software remains functional-Layer/SEED deployment work on the lower path.
Implementation state is recorded only in [`CURRENT.md`](../CURRENT.md).
