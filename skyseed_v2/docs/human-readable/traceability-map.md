# Examples, Tools, and tests

Legend: `A --> B` means A is directly exercised or represented by B in this
example-to-verification traceability map. It does not express imports or
ownership.

```mermaid
flowchart LR
    CE["complete_static_emulator"] --> EM["Emulator + compiler + Compose launcher"]
    CE --> TZ["Topology / Zone Tools"]
    DN["dynamic_slice_network"] --> DS["Simulator + Slicer + Models"]
    DN --> DT["Topology / Zone / Dynamic Slice Tools"]
    IS["isolated_satellites"] --> SE["SEED entity and compiler boundary"]
    LS["large_static_network"] --> SC["configuration + scale construction"]

    EM --> ITEST["integration tests"]
    DS --> UTEST["focused unit tests"]
    TZ --> TTEST["tests/tools"]
    DT --> TTEST
    DT --> BROWSER["browser smoke script"]
    SE --> ITEST
    SC --> ITEST
```

| Change area | Human entry | Primary verification |
|---|---|---|
| Emulator/configuration/SEED | `complete_static_emulator` | entity, construction, compiler integration |
| Simulator/Topology/Zone | `dynamic_slice_network/simu.py` | topology, zone, lifecycle unit tests |
| Slicer/Models/Difference | `dynamic_slice_network` | dynamic main-chain and difference tests |
| Inspection Tools | both complete and dynamic examples | `tests/tools`, tooling boundaries, browser smoke |
| Compose launcher | complete/large examples | launcher and large-network integration |

Exact file-level maintenance ownership lives only in
`../codex-reference/maintenance-map.md`.
Implementation state is recorded only in [`CURRENT.md`](../CURRENT.md).
