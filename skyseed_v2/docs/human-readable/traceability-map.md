# Architecture traceability

| Contract | Primary owner | Main implementation | Reference |
|---|---|---|---|
| formal timestamp sequence | Simulator | `simulator/timeline.py` | [Timeline/Plan](../codex-reference/timeline-plan-contracts.md) |
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
| detached inspection | top-level Tools | `tools/physical_world`, `physical_frame`, `planner`, `bpf_compiler`, `runner`, `observation`, `traffic`, `zone` | [inspection](../codex-reference/inspection-tools.md) |
| public facade | Simulator package | `simulator/__init__.py` | [public imports](../codex-reference/public-imports.md) |

## Frozen cross-layer calls

```text
PhysicalWorld.frame_at(timestamp)
PhysicalLinkModel.compute_observation(physical_world, frame, request)
Planner.observe(frame, observation)
compile_bpf(plan, physical_world, previous_realization)
BpfRunner consumes one sealed typed packed Runner artifact
```

## Canonical qualification facts

The canonical fixture must prove 121 formal frames and observations, 121
transitions, 30 Plans, 30 realizations, and 121 activation timestamps. Each
Plan and realization must replay through continuous world and state lineage.
The exact validation output is stored outside the repository for each run.
