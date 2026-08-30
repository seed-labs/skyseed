# Examples, Tools, and tests

Legend: `A --> B` means A is directly exercised or represented by B in this
example-to-verification traceability map. It does not express imports or
ownership.

```mermaid
flowchart LR
    CE["complete_static_emulator"] --> EM["Emulator + compiler + Compose launcher"]
    CE --> TZ["Topology / Zone Tools"]
    CE --> PL["Simulator Planner canonical path"]
    DN["mini_satellite_internet"] --> DS["Simulator + Slicer + Models"]
    DN --> DT["Topology / Zone / Slice / Difference Tools"]
    DN --> PT["Planner Inspection Tool"]
    DN --> OT["System Observation Tool"]
    DN --> BT["BPF Compiler + Inspection Tool"]
    DN --> BR["BpfRunner runtime"]
    DN --> RI["post-run Runner Inspection Tool"]
    DN --> TR["post-run Traffic / Path Replay Tool"]
    S4["starlink_gen1_satellite_internet"] --> S4S["One selected Gen1 shell + stage-bounded rolling Slicer / Planner / Compiler"]
    S4 --> TZ
    S4 --> DT
    S4 --> OT
    S4 --> PT
    S4 --> BT
    S4 --> BR
    S4 --> RI
    EM --> ITEST["integration tests"]
    DS --> UTEST["focused unit tests"]
    S4S --> UTEST
    PL --> PTEST["planning unit + integration tests"]
    TZ --> TTEST["tests/tools"]
    DT --> TTEST
    DT --> BROWSER["browser smoke script"]
    PT --> TTEST
    OT --> TTEST
    BT --> BTEST["Compiler/Tool focused + browser tests"]
    BR --> RTEST["Runner focused + real kernel/packet validation"]
    RI --> RITEST["Runner Tool + runtime finalization tests"]
    TR --> TRTEST["Traffic replay grouping/interpolation/artifact tests + page review"]
```

| Change area | Human entry | Primary verification |
|---|---|---|
| Emulator/configuration/SEED | `complete_static_emulator` | entity, construction, compiler integration |
| Simulator/Topology/Zone | `mini_satellite_internet/simu.py` and `starlink_gen1_satellite_internet/simu.py` | topology, zone, lifecycle, shell-selection, and scale-input tests |
| Slicer/Models/Difference | `mini_satellite_internet` and Starlink Gen1 `--through slicer` (Zone-service minimum: 25°) | dynamic main-chain, Starlink Gen1 integration, exhaustive-pruning equivalence, and Difference tests |
| Planner/Plan/planning algorithms | `mini_satellite_internet/simu.py` | planning value, algorithm and main-chain tests |
| Planner human inspection | `mini_satellite_internet/main.py` and Starlink Gen1 `--through planner` | Planner Tool tests, detached-handoff tests, and Firefox visual inspection |
| Slicer/Planner execution observation | `mini_satellite_internet/main.py` and Starlink Gen1 `--through slicer|planner|compiler` | Observation record/aggregation/artifact tests and visual page inspection |
| BPF Compiler inspection | `mini_satellite_internet/main.py` and Starlink Gen1 `--through compiler` | Compiler/Tool focused tests, detached-handoff tests, and browser smoke |
| BPF Runner live lifecycle | generated Mini or Starlink Gen1 `start.sh` followed by `run.sh` (or thin `launch.sh`) → private `experiment.execution` composition → `BpfRunner`; Starlink Gen1 consumes its persisted exact Compiler handoff | Runner contract/main-chain, persisted-handoff, bounded-execution, teardown, and focused real generated-workflow validation |
| BPF Runner human inspection | generated Mini or Starlink Gen1 `run.sh` → `inspection/runner/index.html` | Runner Tool projection/artifact/completeness tests, runtime finalization tests, controlled-interruption page review |
| Traffic/path replay | generated Mini `run.sh` → `inspection/traffic/index.html` | clock/grouping/identity/interpolation/artifact tests plus canonical packet-path page review |
| Inspection Tools | all three maintained examples; Starlink Gen1 adds canonical lazy Slice, Planner, Compiler, and Observation views through bounded detached workers | `tests/tools`, detached process/handoff tests, tooling boundaries, browser smoke |
| Compose launcher | `complete_static_emulator` and canonical Mini | launcher and integration tests |

Exact file-level maintenance ownership lives only in
`../codex-reference/maintenance-map.md`.
Implementation state is recorded only in [`CURRENT.md`](../CURRENT.md).
