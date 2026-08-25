# Emulator–Simulator boundary

## Authority

Normative detail under `../accepted-design.md`. Implementation state is
recorded only in [`CURRENT.md`](../CURRENT.md).

## Scope

The only projection from Emulator objects to immutable Simulator input.

## Owners and consumers

`emulator_to_simulator.py` knows the Emulator entity tree and creates
receiver-owned DTOs from `simulator/input/`. Simulator core receives DTOs and
does not import this bridge.

## Contract

```text
explicit SkyseedAutonomousSystem values
→ create_simulator_static_input(...)
→ SimulatorStaticInput(
    SatelliteSimulationInput...,
    GroundStationSimulationInput...
  )
→ SimulationDefinition.static_input
→ Topology materialization
```

The two one-entity functions copy the same approved scalar values. `NodeId`
uses the sole `(asn, entity_kind, entity_id)` derivation rule.

GroundStation `operator` is copied unchanged as explicit descriptive metadata.
A stripped non-empty string means known; `None` means the experiment input does
not know the operator. No layer invents a string sentinel.

GroundStation `max_simultaneous_satellite_accesses` is likewise copied from
Config through the site-level Emulator entity and
`GroundStationSimulationInput` into Topology. `None` means no per-site cap fact
is specified; a positive integer is a hard simulation capability. It is not
derived from physical antenna count.

Satellite `max_simultaneous_ground_station_accesses` is copied through the
same configuration → Emulator entity → `SatelliteSimulationInput` → Topology
chain. `None` means no explicit Satellite-side Gateway-access cap; a positive
integer limits only simultaneous GroundStation-access Connections, not ISLs,
UT service, or total degree.

## Invariants

- formal type is `SimulatorStaticInput`;
- formal aggregate function is `create_simulator_static_input`;
- DTOs contain neutral immutable values, no SEED object/reference;
- duplicate `NodeId`, owner inconsistency, unsupported entity, or invalid
  Orbit parameter fails before publishing input;
- `emulator_to_simulator.py` is the only production module importing both
  Emulator and Simulator-input packages.

## Failure semantics

Unsupported iterables/types raise `TypeError`; DTO/value collisions raise
`ValueError`. There is no partial return or compatibility name.

## Prohibited behavior

No `EmulatorStaticInput`, `create_emulator_static_input`, node index service,
Simulator-to-Emulator reverse read, or bridge logic inside Simulator core.

## Extension points

Adding another Emulator entity projection changes the public cross-layer
contract and requires approval plus a receiver-owned DTO decision.

## Maintenance

Update the bridge, DTO tests, construction-equivalence tests, import graph,
public-import mapping, and examples together.
