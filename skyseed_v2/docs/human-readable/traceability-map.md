# Architecture traceability

| Contract | Primary owner | Main implementation | Reference |
|---|---|---|---|
| finite formal timestamp sequence | Simulator | `simulator/timeline.py` | [Timeline/Plan](../codex-reference/timeline-plan-contracts.md) |
| physical aggregate and frame | Simulator physical | `simulator/physical/world.py`, `physical_frame.py` | [physical contracts](../codex-reference/physical-world-zone-contracts.md) |
| physical candidate observation | Simulator physical + built-in Model | `simulator/physical/physical_link.py`, `models/geometric_physical_link.py` | [Model contracts](../codex-reference/model-contracts.md) |
| operational state and transitions | Simulator planning | `simulator/planning/values.py` | [operational values](../codex-reference/operational-values.md) |
| operational planning and publication | Simulator planning | `simulator/planning/planner.py` | [Planner contracts](../codex-reference/planning-contracts.md) |
| replaceable planning roles | Simulator planning | `simulator/planning/models.py`, `coordination.py` | [Model contracts](../codex-reference/model-contracts.md) |
| planning evidence codecs | Simulator planning | `simulator/planning/evidence.py` | [Planner contracts](../codex-reference/planning-contracts.md) |
| Route outcomes | Simulator planning | `simulator/planning/models.py`, shared routing core | [Route contracts](../codex-reference/routing-path-contracts.md) |
| incremental cross-domain progression | experiment orchestration | `experiment/_realization_producer.py` | [producer/runtime](../codex-reference/producer-runtime-contracts.md) |
| semantic-to-BPF lowering | BPF backend | `backend/bpf/compiler.py` | [Compiler](../codex-reference/bpf-compiler.md) |
| compiled intended state | BPF backend | `backend/bpf/compiled_realization.py` | [Compiler](../codex-reference/bpf-compiler.md) |
| sealed Runner artifact | BPF backend private boundary | `backend/bpf/_runner_artifact.py` | [producer/runtime](../codex-reference/producer-runtime-contracts.md) |
| live BPF lifecycle | BPF backend | `backend/bpf/runner.py`, `_runner/` | [ownership](../codex-reference/ownership-lifecycle.md) |
| Traffic classification | experiment Traffic | `experiment/traffic/`, `tools/traffic/` | [inspection](../codex-reference/inspection-tools.md) |
| detached inspection | top-level Tools | `tools/physical_world`, `planner`, `bpf_compiler`, `runner`, `observation`, `traffic`, `zone` | [inspection](../codex-reference/inspection-tools.md) |
| public facade | Simulator package | `simulator/__init__.py` | [public imports](../codex-reference/public-imports.md) |

## Frozen cross-layer calls

```text
PhysicalWorld.frame_at(timestamp)
PhysicalLinkModel.compute_observation(physical_world, frame, request)
Planner.observe(frame, observation)
compile_bpf(plan, physical_world, previous_realization)
BpfRunner consumes one sealed typed packed Runner artifact
```

The Runner constructor takes `compose_project_name` and the finite
`runtime_artifact` manifest path. Indexed decoding completes in Preparation,
which physically activates B0; formal Main executes B1 and later prepared
deltas. Private artifact, Linux execution, startup identity and observation
files are mapped under their existing owner in the
[repository map](repository-map.md).

## Canonical qualification facts

The canonical fixture must prove 121 formal frames and observations, 121
transitions, four Plans, four realizations, and 121 activation timestamps. Each
Plan and realization must replay through continuous world and state lineage.
The exact validation output is stored outside the repository for each run.

The [S1–S5 Example](../../examples/starlink_multi_shell_satellite_internet/README.md)
exercises existing owners in one shared network with consecutive sealed
deliveries. `test_runner_batches.py` compares semantic fingerprints and covers
multiple handoffs/reclaim cycles and deadline exhaustion;
`test_starlink_multi_shell_example.py` verifies shared inventory, within-Shell
pairs and two-delivery packaging. Live helper/verifier tests cover retained
connections and bounded kernel retirement. This adds no owner mapping.
