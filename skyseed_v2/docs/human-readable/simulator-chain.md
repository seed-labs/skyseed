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
advances ISL lifecycle, selects GroundStation accesses, assigns Zone service,
updates effective parameters, plans Routes over operational Connections, and
creates one formal transition.

The physical-observation frontier may advance ahead of the publication
frontier. Canonical window policy plans with 50 seconds of context and publishes
the earliest contiguous 20-second prefix. Each event retains its 5-second
formal timestamp.

## Warm start and finish

The producer samples at most the Planner-required bounded prehistory and calls
`prime` before formal progression. Warm samples do not become formal
transitions. When the Timeline iterator ends, the producer calls `finish` once
and drains the remaining Plans.

## Persistence

Each compiled realization is appended to the deterministic versioned handoff.
Before Runner construction, generated execution eagerly validates every hash,
structurally decodes the full bounded sequence, and validates lineage. Runner then
pulls only from the validated immutable tuple. Semantic and persistence work is
complete before runtime maintenance begins.

The example entry points in `examples/` expose this same chain at different
scales; none implements a second planning or compilation algorithm.
