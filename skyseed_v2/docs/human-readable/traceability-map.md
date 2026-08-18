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
    DN --> DT["Topology / Zone / Dynamic Slice Tools"]
    DN --> PT["Planner Inspection Tool"]
    DN --> BT["BPF Compiler + Inspection Tool"]
    IS["isolated_satellites"] --> SE["SEED entity and compiler boundary"]
    LS["large_static_network"] --> SC["configuration + scale construction"]

    EM --> ITEST["integration tests"]
    DS --> UTEST["focused unit tests"]
    PL --> PTEST["planning unit + integration tests"]
    TZ --> TTEST["tests/tools"]
    DT --> TTEST
    DT --> BROWSER["browser smoke script"]
    PT --> TTEST
    BT --> BTEST["Compiler/Tool focused + browser tests"]
    SE --> ITEST
    SC --> ITEST
```

| Change area | Human entry | Primary verification |
|---|---|---|
| Emulator/configuration/SEED | `complete_static_emulator` | entity, construction, compiler integration |
| Simulator/Topology/Zone | `mini_satellite_internet/simu.py` | topology, zone, lifecycle unit tests |
| Slicer/Models/Difference | `mini_satellite_internet` | dynamic main-chain and difference tests |
| Planner/Plan/planning algorithms | `mini_satellite_internet/simu.py` | planning value, algorithm and main-chain tests |
| Planner human inspection | `mini_satellite_internet/main.py` | Planner Tool tests and visual artifact inspection |
| BPF Compiler inspection | `mini_satellite_internet/main.py` | Compiler/Tool focused tests and browser smoke |
| Inspection Tools | both complete and dynamic examples | `tests/tools`, tooling boundaries, browser smoke |
| Compose launcher | complete/large examples | launcher and large-network integration |

Exact file-level maintenance ownership lives only in
`../codex-reference/maintenance-map.md`.
Implementation state is recorded only in [`CURRENT.md`](../CURRENT.md).
