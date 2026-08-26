# System Observation Tool

This repository-local Tool answers three diagnostic questions about an actual
execution: what work ran, how much work ran, and what that work cost. It reads
only detached JSONL records written after domain work completed. It
never executes or re-executes a Slicer, Planner, Compiler, Runner, or Model.

The record format is intentionally small and generic: run metadata, component,
stage, bootstrap/steady/idle phase, logical scope, monotonic duration, named
numeric work measurements, and optional resource measurements. Metric names
belong to the emitting component; they are not fields on `TopologySlice`,
`TopologyDifference`, `Plan`, or another semantic value.
Each duration is labeled as an exclusive stage, overlapping operation rollup,
or resource sample so summaries do not silently add parent and child timing.

```text
private execution observer → execution-observations.jsonl
                           → reader → aggregation → renderer → artifacts
```

Current emitters are Slicer, Planner, and the BPF Compiler. Slicer records coarse Position,
Connection, Route, validation, publication, complete-Slice, and Difference
stages. Planner records Difference acquisition, window selection, route
context, three planning domains, coordination, precedence, state advancement,
validation, publication, and total synchronization. Compiler records its
exclusive lowering/validation/freeze stages and overlapping total-compile
rollup. The accepted dynamic S4 qualification also records its
already-available RSS/peak-RSS/host-memory samples as resource observations.
Future Runner support extends the same component/stage/work/resource vocabulary
without changing formal domain schemas.

`write_observation_tool(records_path, output_directory)` writes the canonical
JSONL, aggregate JSON, compact external browser record script, standalone HTML,
summary, inventory, and checksums. Bootstrap per-series run durations remain
separate from steady p50/p95/max; p95 uses nearest rank. Leaf stages and
overlapping rollups are independent series, resource samples have no duration
aggregate, and the Tool infers no component or pipeline total. Slicer
`complete-slice` and `difference` are separate top-level series; Planner
`total-synchronize` is its top-level rollup. Time-per-work views use only
positive work recorded on leaf-stage records.

The unified page has two complementary views. Stage progression positions
Slicer points by Slice ID/timestamp and Planner points by publication coverage,
so components referring to the same simulation region share an actual
simulation-time axis rather than a record-sequence axis. Its default duration
axis is shared by Slicer and Planner, uses automatically selected numeric
ticks, and derives the formal Slice-interval reference from record metadata or
Slice timestamps; `Fit selection` changes only the view scale.

Per-Slice / Plan breakdown selects either one Slicer Slice (`S1` …) or one
Planner publication (`P0` …) through a horizontal visual scrubber, with the
dropdown retained as secondary navigation. A bounded stage×unit heatmap keeps
missing records distinct from exact measured zero and places overlapping
rollups in a separate lower band. The selected unit shows every attached
observation, the primary rollup, its exclusive leaf subtotal, and an exact
non-negative unattributed gap when the measurements support one. Rollup
envelopes, zero-duration samples, and Slicer Difference work remain visually
separate; the independent Difference and overlapping rollups are never added
to leaf time.
Chart-point selection creates a persistent annotation with unit, coverage,
duration, phase, and work evidence until another point is selected or the
label is cleared. Future components can reuse the same convention once they
emit a natural logical-unit scope.
