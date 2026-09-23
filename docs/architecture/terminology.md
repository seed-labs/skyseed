# Terminology and source map

| Term | Meaning | Source responsibility |
|---|---|---|
| Emulator entity | Editable SEED scene participant, with satellite extension nodes and router-based GSs | [emulator](../../src/skyseed/emulator/README.md) |
| Static input | Receiver-owned immutable physical declarations, independent of SEED | [input/adapters](../../src/skyseed/adapters/README.md) |
| PhysicalWorld / snapshot / candidate | Static physical aggregate / positions at a time / feasible relationship | [physical](../../src/skyseed/simulator/physical/README.md) |
| Selection / preparation / operational connection | Policy choice / capacity reservation while acquiring / usable planned edge | [Planner](../../src/skyseed/simulator/planner/README.md) |
| State / Transition / Plan | Current operational outputs / one formal delta / contiguous published deltas with lineage | [planning contract](../development/planner-state-and-plans.md) |
| Compiler result / execution materials | Backend intentions / sealed serialized Runner input | [backend](../../src/skyseed/backend/README.md) |
| Data plane | General packet-forwarding mechanism, implemented here by BPF or Linux | [backend](../../src/skyseed/backend/README.md) |
| `DataPlane` authoring type (BPF) | Selectable C/eBPF handler, shared completion, material translation and Runner-owned adaptation | [DataPlane](../../src/skyseed/backend/bpf/dataplane/README.md) |
| Producer / Controller | Calculation cursor owner / experiment integration and publication | [experiment](../../src/skyseed/experiment/README.md) |
| Deployer | Static build, deployment inspection, startup and teardown | [deployment](../../src/skyseed/deployment/README.md) |
| Runner ACTIVE | Last fully completed actual network boundary, including withdrawal | [Runner](../../src/skyseed/backend/runner/README.md) |
| Initial range / requested endpoint / completed range | Original immutable Timeline / admitted calculation target / published results safe for readers | [experiment contract](../development/experiment-control.md) |
| Continue / resume | Calculate additional timestamps / release Runner's user pause | [controls guide](../user-guides/computation-and-controls.md) |
| Interaction client / owner consumer | Read/publish/request via Etcd / admit and dispatch into the actual computation or runtime owner | [experiment source map](../../src/skyseed/experiment/README.md#interaction-and-revision-owners) |
| Receipt / result / observation | Stored request identity / reported effect and history / owner-local facts with time, session and freshness | [interaction contract](../development/experiment-control.md#shared-interaction-and-process-authority) |
| Revision / selected material / frozen window | Complete dependent-suffix recalculation / authoritative file version / window whose preparation has begun | [revision contract](../development/experiment-control.md#revision-and-reusable-continuation) |

`configuration/` parses declarations and generates Zone layouts. `simulator/geometry/`
contains physical coordinate mathematics. `shared/` holds genuinely neutral values and
mechanical support. `tools/observation/` owns detached presentation; `tools/generation/traffic` owns workload generation;
`tools/development/` contains independent authoring checks; `templates/models/`
holds copyable minimal roles, while real S3/S5 Models live beside those examples. `data/`
holds runtime inputs; `research/` records external evidence. None is a plugin registry.
