# Accepted Architecture Anchor

This short anchor calibrates each new vertical slice and important
investigation. It does not replace `CURRENT.md`, the conceptual design or the
accepted class diagrams.

## Accepted objects and ownership

The accepted Simulator objects are `Simulator`, `SimulationTimeline`,
`SimulationDefinition`, `Topology`, the static Simulator
`SkyseedAutonomousSystem` / `Shell` / `Orbit` / `Satellite` /
`GroundStation` tree, `ZoneLayout`, `Zone`,
`LatitudeLongitudeRectangle`, `ZoneMembershipModel` and
`DefaultZoneMembershipModel`.

- A `Simulator` explicitly and atomically creates at most one `Topology`.
- `Topology` owns the static object tree and at most one `ZoneLayout`.
- `ZoneLayout` owns its `Zone` tuple, read-only index and fixed Model.
- The Model owns the membership/resolution algorithm, not Zone data.
- Simulator core has no dependency on repository-level `tools`.

## Accepted creation chain

The formal external construction path is:

```text
Emulator
→ SatelliteBase-owned Skyseed AS tuple
→ create_simulator_static_input
→ SimulatorStaticInput + SimulationTimeline
→ SimulationDefinition
→ Simulator
→ Simulator.create_topology(...)
→ Topology
```

Zone definitions are supplied to `Simulator.create_topology(...)`; the
resulting optional `ZoneLayout` is owned by that `Topology`.

## Future runtime direction

The accepted direction for later holistic design is:

```text
Topology
→ Slicer
   → position prerequisite computation
   → connection
   → ConnectionSlice
   → route
   → RouteSlice
   → TopologySlice
```

This direction is not approval to implement those concepts in the current
tooling round.

## Retired or prohibited directions

- Do not elevate Satellite Position into the next standalone top-level
  development module.
- Do not store dynamic Zone membership on `Satellite` or `GroundStation`.
- Do not make a Model a second owner of Zone data.
- Do not make `tools` a formal computation path or fallback.
- Do not automatically carry v1 types or algorithms into v2.

After the Tooling Baseline, the next development entry is a fresh holistic
design of Slicer and dynamic Slice behavior.

## Information confidence

When sources disagree, use this order:

1. explicit user decisions;
2. this anchor, accepted class diagrams and frozen public contracts;
3. accepted code and independent audits;
4. Codex repository facts;
5. Codex design recommendations;
6. v1 and other historical references.

Investigation reports must label **Repository facts**, **Design candidates**
and **Historical/reference facts** separately. Candidates and historical
facts do not modify the accepted architecture without an explicit decision.
