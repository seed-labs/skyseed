# Terminology and source map

| Term | Meaning | Source responsibility |
|---|---|---|
| Emulator entity | Editable SEED scene participant, with satellite extension nodes and router-based GSs | [emulator](../../src/skyseed_v2/emulator/README.md) |
| Static input | Receiver-owned immutable physical declarations, independent of SEED | [input/adapters](../../src/skyseed_v2/adapters/README.md) |
| PhysicalWorld / snapshot / candidate | Static physical aggregate / positions at a time / feasible relationship | [physical](../../src/skyseed_v2/simulator/physical/README.md) |
| Selection / preparation / operational connection | Policy choice / capacity reservation while acquiring / usable planned edge | [Planner](../../src/skyseed_v2/simulator/planner/README.md) |
| State / Transition / Plan | Current operational outputs / one formal delta / contiguous published deltas with lineage | [planning contract](../development/planner-state-and-plans.md) |
| Compiler result / execution materials | Backend intentions / sealed serialized Runner input | [backend](../../src/skyseed_v2/backend/README.md) |
| Data plane | General packet-forwarding mechanism, implemented here by BPF or Linux | [backend](../../src/skyseed_v2/backend/README.md) |
| `DataPlane` authoring type (BPF) | Selectable C/eBPF handler, shared completion, material translation and Runner-owned adaptation | [DataPlane](../../src/skyseed_v2/backend/bpf/dataplane/README.md) |
| Producer / Controller | Calculation cursor owner / experiment integration and publication | [experiment](../../src/skyseed_v2/experiment/README.md) |
| Deployer | Static build, deployment inspection, startup and teardown | [deployment](../../src/skyseed_v2/deployment/README.md) |
| Runner ACTIVE | Last fully completed actual network boundary, including withdrawal | [Runner](../../src/skyseed_v2/backend/runner/README.md) |
| Initial range / requested endpoint / completed range | Original immutable Timeline / admitted calculation target / published results safe for readers | [experiment contract](../development/experiment-control.md) |
| Continue / resume | Calculate additional timestamps / release Runner's user pause | [controls guide](../user-guides/computation-and-controls.md) |

`configuration/` parses declarations and generates Zone layouts. `simulator/geometry/`
contains physical coordinate mathematics. `shared/` holds genuinely neutral values and
mechanical support. `tools/observation/` owns detached presentation; `tools/generation/traffic` owns workload generation;
`tools/development/` contains independent authoring checks; `templates/models/`
holds copyable minimal roles, while real S3/S5 Models live beside those examples. `data/`
holds runtime inputs; `research/` records external evidence. None is a plugin registry.
