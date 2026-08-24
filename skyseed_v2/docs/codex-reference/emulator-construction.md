# Emulator construction contract

## Authority

Normative detail under `../accepted-design.md`. Implementation state is
recorded only in [`CURRENT.md`](../CURRENT.md).

## Scope

Construction of one upstream SEED `Emulator` from one immutable
`StaticEmulatorConfig`. This contract ends before Emulator-to-Simulator
projection, Docker compilation, Compose launch, or dynamic slicing.

## Owners and consumers

`skyseed_v2.emulator.create_static_emulator` is the public entry point.
`emulator/construction.py` owns the implementation. Examples and application
assembly create configuration values and consume the returned upstream
`Emulator`; construction owns no later lifecycle.

The upstream `Base` owns created Skyseed and ordinary autonomous systems.
`SkyseedAutonomousSystem` owns Shells and GroundStations, each Shell owns
Orbits, and each Orbit owns Satellites. Routing layers remain upstream SEED
objects attached to the returned Emulator.

`get_skyseed_autonomous_systems(base)` is the Skyseed-owned read-only query
used by construction and external assembly examples. It enumerates upstream
Base AS values and returns only `SkyseedAutonomousSystem` instances; it owns
no entity, registration, or lifecycle behavior.

## Contract

`create_static_emulator(config)`:

1. requires one `StaticEmulatorConfig`;
2. creates one upstream `Emulator` and one upstream `Base`;
3. creates Internet Exchanges and ordinary Autonomous Systems;
4. creates each Skyseed Autonomous System through the established
   AS → Shell → Orbit → Satellite owner chain, plus AS-owned GroundStations;
5. creates routing layers and eBGP peerings from configuration;
6. returns the complete upstream Emulator.

Generated Orbit layouts expand deterministically into private scalar inputs
before entity creation. The public facade delegates to the implementation
function object; callers do not import `emulator.construction`.

## Invariants

- owner-local creation APIs enforce identity and uniqueness;
- configuration is read, never mutated;
- no Simulator object or input DTO is created;
- no compiler, Compose launcher, Tool, Planner, or runtime executor is called;
- ordinary and Skyseed AS construction remains explicit;
- no partially constructed Emulator is returned after failure.

## Failure semantics

A non-`StaticEmulatorConfig` raises `TypeError`. Invalid configuration has
already failed at configuration construction/YAML loading. Entity collision,
invalid owner relationship, invalid Orbit values, or upstream SEED rejection
propagates from the owning boundary. There is no fallback configuration,
legacy constructor, compatibility alias, or partial return.

## Prohibited behavior

No Simulator projection, static-input DTO creation, Docker compilation,
Compose launch, Slice calculation, example-specific branch, mutable
global registry, forwarding import, or alternative owner tree.

## Extension points

New declaration fields may be consumed only after their configuration and
entity owner contracts are approved. Private deterministic expansion helpers
may change without creating a second public construction entry.

## Maintenance

Update `emulator/construction.py`, the `skyseed_v2.emulator` export,
configuration/entity contracts, Emulator chain/class diagrams, construction
and equivalence tests, examples, and public-import mapping together.
