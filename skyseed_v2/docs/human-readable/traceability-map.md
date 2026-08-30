# Examples, Tools, and tests

Legend: `A --> B` means A is directly exercised or represented by B in this
example-to-verification traceability map. It does not express imports or
ownership.

```mermaid
flowchart LR
    CE["complete_static_emulator"] --> EM["Emulator + compiler + Compose launcher"]
    CE --> TZ["Topology / Zone Tools"]
    CE --> PF["Physical Frame Tool"]
    CE --> PL["Simulator Planner canonical path"]
    DN["mini_satellite_internet"] --> DS["Simulator + Topology / PhysicalFrame + temporary legacy Slicer / Models"]
    DN --> TZ
    DN --> LT["temporary legacy Slice / Difference Tool"]
    DN --> PT["Planner Inspection Tool"]
    DN --> OT["System Observation Tool"]
    DN --> BT["BPF Compiler + Inspection Tool"]
    DN --> BR["BpfRunner runtime"]
    DN --> RI["post-run Runner Inspection Tool"]
    DN --> TR["post-run Traffic / Path Replay Tool"]
    S4["starlink_gen1_satellite_internet"] --> S4S["One selected Gen1 shell + physical frames + stage-bounded temporary legacy Slicer / Planner / Compiler"]
    S4 --> TZ
    S4 --> PF
    S4 --> LT
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
    PF --> TTEST
    LT --> TTEST
    LT --> BROWSER["browser smoke script"]
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
| Simulator/Topology/PhysicalFrame/Zone | `complete_static_emulator/simu.py`, `mini_satellite_internet/simu.py`, and `starlink_gen1_satellite_internet/simu.py` | Topology-owned PositionModel/frame-at/PhysicalFrame, Zone, lifecycle, shell-selection, and scale-input tests |
| Physical Frame human inspection | `complete_static_emulator/main.py` and Starlink Gen1 `--through topology` | Physical Frame Tool exact-input, physical-only schema, artifact, and no-authoritative-projection boundary tests |
| Temporary legacy Slicer/Connection/Route/Slice/Difference | `mini_satellite_internet` and Starlink Gen1 `--through slicer` (Zone-service minimum: 25°) | dynamic main-chain, Starlink Gen1 integration, exhaustive-pruning equivalence, Difference, and legacy Tool tests until removal |
| Planner/Plan/planning algorithms | `mini_satellite_internet/simu.py` | planning value, algorithm and main-chain tests |
| Planner human inspection | `mini_satellite_internet/main.py` and Starlink Gen1 `--through planner` | Planner Tool tests, detached-handoff tests, and Firefox visual inspection |
| Temporary Slicer/Planner execution observation | `mini_satellite_internet/main.py` and Starlink Gen1 `--through slicer|planner|compiler` | Observation record/aggregation/artifact tests and visual page inspection |
| BPF Compiler inspection | `mini_satellite_internet/main.py` and Starlink Gen1 `--through compiler` | Compiler/Tool focused tests, detached-handoff tests, and browser smoke |
| BPF Runner live lifecycle | generated Mini or Starlink Gen1 `start.sh` followed by `run.sh` (or thin `launch.sh`) → private `experiment.execution` composition → `BpfRunner`; Starlink Gen1 consumes its persisted exact Compiler handoff | Runner contract/main-chain, persisted-handoff, bounded-execution, teardown, and focused real generated-workflow validation |
| BPF Runner human inspection | generated Mini or Starlink Gen1 `run.sh` → `inspection/runner/index.html` | Runner Tool projection/artifact/completeness tests, runtime finalization tests, controlled-interruption page review |
| Traffic/path replay | generated Mini `run.sh` → `inspection/traffic/index.html` | clock/grouping/identity/interpolation/artifact tests plus canonical packet-path page review |
| Inspection Tools | all three maintained examples; complete and Starlink Gen1 expose the one-frame physical view, while Mini and Starlink Gen1 temporarily retain lazy mixed-state Slice, Planner, Compiler, and Observation views | `tests/tools`, detached process/handoff tests, tooling boundaries, browser smoke |
| Compose launcher | `complete_static_emulator` and canonical Mini | launcher and integration tests |

Exact file-level maintenance ownership lives only in
`../codex-reference/maintenance-map.md`.
Implementation state is recorded only in [`CURRENT.md`](../CURRENT.md).
