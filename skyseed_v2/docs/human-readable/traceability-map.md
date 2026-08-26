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
    S4["s4_satellite_internet"] --> S4S["Static Topology + S4-181 Slicer / Planner / Compiler qualification"]
    S4 --> TZ
    S4 --> DT
    S4 --> OT
    S4 --> PT
    S4 --> BT
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
| Simulator/Topology/Zone | `mini_satellite_internet/simu.py` and `s4_satellite_internet/simu.py` | topology, zone, lifecycle, and S4 scale-input tests |
| Slicer/Models/Difference | `mini_satellite_internet` and opt-in dynamic S4 (final S4 Zone-service minimum: 25°) | dynamic main-chain, S4 integration, exhaustive-pruning equivalence, and Difference tests |
| Planner/Plan/planning algorithms | `mini_satellite_internet/simu.py` | planning value, algorithm and main-chain tests |
| Planner human inspection | `mini_satellite_internet/main.py` and S4 `--planner` | Planner Tool tests, detached-handoff tests, and Firefox visual inspection |
| Slicer/Planner execution observation | `mini_satellite_internet/main.py` and opt-in dynamic S4 | Observation record/aggregation/artifact tests and visual page inspection |
| BPF Compiler inspection | `mini_satellite_internet/main.py` and S4 `--planner` | Compiler/Tool focused tests, detached-handoff tests, and browser smoke |
| BPF Runner live lifecycle | generated Mini `start.sh` → `run.sh` → root-private `_generated_bpf_execution` application composition → `BpfRunner` | Runner contract/main-chain tests and focused real generated-workflow validation |
| BPF Runner human inspection | generated Mini `run.sh` → `inspection/runner/index.html` | Runner Tool projection/artifact tests, runtime finalization tests, controlled-interruption page review |
| Traffic/path replay | generated Mini `run.sh` → `inspection/traffic/index.html` | clock/grouping/identity/interpolation/artifact tests plus canonical packet-path page review |
| Inspection Tools | all three maintained examples; S4 adds canonical lazy Slice, Planner, Compiler, and Observation views through bounded detached workers | `tests/tools`, detached process/handoff tests, tooling boundaries, browser smoke |
| Compose launcher | `complete_static_emulator` and canonical Mini | launcher and integration tests |

Exact file-level maintenance ownership lives only in
`../codex-reference/maintenance-map.md`.
Implementation state is recorded only in [`CURRENT.md`](../CURRENT.md).
