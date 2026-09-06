# `skyseed_v2` package map

Production dependencies flow from declarations and Emulator construction into
immutable Simulator input, physical/planned semantics, BPF compilation, and
runtime realization.

Legend: `-->` is the supported one-way production dependency and semantic
handoff direction.

```mermaid
flowchart LR
    CFG["configuration"] --> EMU["emulator construction"]
    EMU --> BRIDGE["emulator_to_simulator"]
    BRIDGE --> INPUT["SimulatorStaticInput"]
    INPUT --> SIM["Simulator"]
    SIM --> PW["PhysicalWorld / PhysicalFrame"]
    PW --> OBS["PhysicalLinkObservation"]
    OBS --> PL["Planner / Plan"]
    PL --> COMP["backend/bpf Compiler"]
    COMP --> REAL["BpfCompiledRealization"]
    REAL --> ART["sealed Runner artifact"]
    ART --> RUN["BpfRunner"]
```

`experiment/_realization_producer.py` is the private cross-domain composition
that owns the Timeline cursor, calls the physical-link Model and Planner, and
compiles published Plans. Precomputed generation drains it completely and seals
a strict versioned typed packed Runner artifact. Generated execution verifies
and supplies that finite artifact to Runner; no producer remains at runtime.

`compose/` is an optional deployment utility and owns no semantic or Runner
lifecycle. `shared/` contains only narrow neutral values and private mechanical
helpers.

Primary public facades are `skyseed_v2.emulator`, `skyseed_v2.simulator`, and
`skyseed_v2.backend.bpf`. Repository-local Tools are detached observers rather
than installed domain APIs.

Architecture: `docs/accepted-design.md`. Public imports:
`docs/codex-reference/public-imports.md`.
