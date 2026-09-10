# Simulator chain

The maintained Simulator call chain is direct and singular.

```python
simulator = Simulator(definition)
physical_world = simulator.create_physical_world(
    position_model=position_model,
    zone_layout_definition=zone_layout,
)
planner = simulator.create_planner(
    physical_link_model=physical_link_model,
    inter_satellite_model=isl_model,
    ground_station_model=ground_station_model,
    zone_service_model=zone_service_model,
    parameter_model=parameter_model,
    route_model=route_model,
    coordination_policy=coordination_policy,
    planning_window_policy=window_policy,
    route_demands=route_demands,
)
```

The producer then references the exact instances:

```text
timeline.iter_timestamps()
→ physical_world.frame_at(timestamp)
→ physical_link_model.compute_observation(...)
→ planner.observe(frame, observation)
→ zero or more Plans
→ compile_bpf(plan, physical_world, previous_realization)
```

## Per-frame semantics

Planner first creates a bounded physical-link request. The physical Model
evaluates only that request and returns sparse feasible candidates. Planner
admits new ISL/GS candidates using physical contact proofs, advances both kinds
of capacity-reserving preparation, assigns Zone service,
updates effective parameters, plans Routes over operational Connections, and
creates one formal transition.

The physical-observation frontier may advance ahead of the publication
frontier. Canonical batching publishes each 32-frame prefix immediately and
flushes the last short batch once. Each event retains its 5-second formal time.

## Warm start and finish

The physical Model first predicts finite contacts with dependency-derived
admission tails. The producer samples the Planner-required preparation history and calls
`prime` before formal progression. Warm samples do not become formal
transitions. When the Timeline iterator ends, the producer calls `finish` once
and drains the remaining Plans.

## Persistence

While the producer advances, the private BPF artifact writer incrementally
normalizes each compiled realization into deterministic typed packed runtime
records and accumulates finite capacity demand. After each finite delivery drain,
it validates and seals the batch, retaining continuation state for its successor. Generated execution verifies source
and input identity plus artifact hashes and metadata before Runner construction.

`BpfRunner` receives the sealed artifact path. Preparation opens the first binary read-only with `mmap`, fully decodes that
delivery, constructs its immutable activation commands, prewrites semantics and
activates B0. For continuation, Maintenance repeats only admitted batch work
against the same initialized runtime and fixed clock. Reader indexing does not defer
decoding into formal execution. `run()` begins the formal clock and executes
B1 and later prepared deltas. There is no realization-object tuple handoff,
runtime semantic producer or second planning authority. Maintenance loads sealed
successors and safely reclaims retired runtime data; Main alone activates.

The example entry points in `examples/` expose this same chain at different
scales; none implements a second planning or compilation algorithm.
